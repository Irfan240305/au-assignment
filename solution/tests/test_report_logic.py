from datetime import datetime, timezone

from solution.pipeline.reports import (Call, Catalogue, Event, build_calls, classify_bucket,
                                       effective_disposition, link_events)

WF = "motor_renewal"
LEAD = (WF, "hashA")


def ev(event_id, event_type, at, code=None, call="c1", phone="hashA", wf=WF):
    return Event(event_id, event_type, wf, call, at, phone, code)


def catalogue():
    cat = Catalogue()
    for code, terminal, success in [("renewed", 1, 1), ("not_interested", 1, 0), ("callback_requested", 0, 0)]:
        cat.known.add((WF, code))
        if terminal:
            cat.terminal.add((WF, code))
        if success:
            cat.success.add((WF, code))
    return cat


def call(failed=False, disposition=None):
    return Call("c", LEAD, datetime(2026, 10, 1, tzinfo=timezone.utc), False, failed, disposition)


# ---- effective disposition

def test_latest_set_without_override():
    events = [ev("e1", "disposition.set", "2026-10-01T10:00:00Z", "callback_requested"),
              ev("e2", "disposition.set", "2026-10-01T11:00:00Z", "renewed")]
    assert effective_disposition(events) == "renewed"


def test_override_beats_later_set():
    events = [ev("e1", "disposition.override", "2026-10-01T09:00:00Z", "not_interested", phone=None),
              ev("e2", "disposition.set", "2026-10-01T11:00:00Z", "renewed")]
    assert effective_disposition(events) == "not_interested"


def test_latest_override_wins_regardless_of_input_order():
    events = [ev("e2", "disposition.override", "2026-10-02T00:00:00Z", "renewed", phone=None),
              ev("e1", "disposition.override", "2026-10-01T00:00:00Z", "not_interested", phone=None)]
    assert effective_disposition(events) == "renewed"


def test_no_disposition():
    assert effective_disposition([]) is None


# ---- buckets

def test_buckets_first_match_wins():
    cat = catalogue()
    assert classify_bucket([], WF, cat) == "yet_to_reach_out"
    # terminal beats "all telephony failed"
    assert classify_bucket([call(failed=True, disposition="not_interested")], WF, cat) == "completed"
    assert classify_bucket([call(failed=True), call(failed=True)], WF, cat) == "in_progress_telephony_failed"
    assert classify_bucket([call(failed=True), call()], WF, cat) == "in_progress_dialed"
    assert classify_bucket([call(disposition="callback_requested")], WF, cat) == "in_progress_dialed"


# ---- linking

def test_override_links_through_call_id_and_orphans_counted():
    events = [
        ev("e1", "call.initiated", "2026-10-01T10:00:00Z"),
        ev("e2", "disposition.override", "2026-10-01T12:00:00Z", "renewed", phone=None),
        ev("e3", "call.initiated", "2026-10-01T10:00:00Z", call="c9", phone="unknown"),
        ev("e4", "disposition.override", "2026-10-01T12:00:00Z", "renewed", call="c9", phone=None),
    ]
    linked, orphans = link_events(events, {LEAD})
    assert {e.event_id for e, _ in linked} == {"e1", "e2"}
    assert orphans == 2  # e3 has an unknown phone, e4's call has no linked events


def test_unknown_dispositions_ignored_and_calls_need_initiated():
    events = [
        ev("e1", "call.initiated", "2026-10-01T10:00:00Z"),
        ev("e2", "call.connected", "2026-10-01T10:00:05Z"),
        ev("e3", "disposition.set", "2026-10-01T10:05:00Z", "renewed"),
        ev("e4", "disposition.override", "2026-10-01T11:00:00Z", "RENEWD", phone=None),  # unknown
        ev("e5", "call.connected", "2026-10-01T10:00:05Z", call="c2"),                    # no initiated
    ]
    linked, _ = link_events(events, {LEAD})
    calls, unknown = build_calls(linked, catalogue())
    assert unknown == 1
    assert len(calls) == 1
    assert calls[0].connected and calls[0].disposition == "renewed"