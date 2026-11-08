from __future__ import annotations

from datetime import timedelta

from support import WHEN, make_edit

from wikihose.clock import VirtualClock
from wikihose.live_stats import LiveStats
from wikihose.store import Store


def test_per_minute_counts_the_last_sixty_seconds(clock: VirtualClock) -> None:
    stats = LiveStats("enwiki", clock)
    for _ in range(5):
        stats.add(make_edit())
        clock.advance(10)
    # arrivals at 0, 10, 20, 30, 40; now is 50
    assert stats.per_minute() == 5
    clock.advance(15)  # 65: the arrival at 0 has left the window
    assert stats.per_minute() == 4
    clock.advance(100)
    assert stats.per_minute() == 0


def test_series_has_sixty_ten_second_buckets_and_rolls_over(clock: VirtualClock) -> None:
    stats = LiveStats("enwiki", clock)
    stats.add(make_edit())
    stats.add(make_edit())
    assert stats.series() == [0] * 59 + [2]
    clock.advance(10)
    stats.add(make_edit())
    assert stats.series() == [0] * 58 + [2, 1]
    clock.advance(25)  # two empty buckets in between
    assert stats.series()[-4:] == [2, 1, 0, 0]
    clock.advance(600)  # ten minutes later everything has rolled out
    assert stats.series() == [0] * 60
    assert len(stats.series()) == 60


def test_the_first_bucket_boundary_is_by_clock_not_by_arrival_count(clock: VirtualClock) -> None:
    stats = LiveStats("enwiki", clock)
    start = int(clock.monotonic() // 10)
    stats.add(make_edit())
    clock.advance(9.9)
    stats.add(make_edit())
    assert int(clock.monotonic() // 10) == start
    assert stats.series()[-1] == 2


def test_top_counts_articles_over_fifteen_minutes(clock: VirtualClock) -> None:
    stats = LiveStats("enwiki", clock)
    for title in ["B", "A", "A", "C", "A", "B"]:
        stats.add(make_edit(title))
        clock.advance(1)
    assert stats.top(2) == [("A", 3), ("B", 2)]
    assert stats.top(10) == [("A", 3), ("B", 2), ("C", 1)]


def test_top_ignores_other_namespaces_and_normalizes_titles(clock: VirtualClock) -> None:
    stats = LiveStats("enwiki", clock)
    stats.add(make_edit("ada_Lovelace"))
    stats.add(make_edit("Ada  Lovelace"))
    stats.add(make_edit("Talk:Ada Lovelace", namespace=1))
    assert stats.top(5) == [("Ada Lovelace", 2)]
    latest = stats.latest("Ada Lovelace")
    assert latest is not None
    assert latest.title == "Ada  Lovelace"


def test_top_forgets_edits_after_fifteen_minutes(clock: VirtualClock) -> None:
    stats = LiveStats("enwiki", clock)
    stats.add(make_edit("Old"))
    clock.advance(14 * 60)
    stats.add(make_edit("New"))
    assert [t for t, _ in stats.top(5)] == ["New", "Old"]
    clock.advance(61)  # "Old" is now 15 minutes and 1 second old
    assert stats.top(5) == [("New", 1)]
    assert stats.latest("Old") is None


def test_split_counts_people_and_bots_over_all_namespaces(clock: VirtualClock) -> None:
    stats = LiveStats("enwiki", clock)
    stats.add(make_edit("A"))
    stats.add(make_edit("B", bot=True))
    stats.add(make_edit("Talk:C", namespace=1, bot=True))
    stats.add(make_edit("D"))
    assert stats.split() == (2, 2)
    clock.advance(16 * 60)
    assert stats.split() == (0, 0)


def test_out_of_order_timestamps_do_not_matter(clock: VirtualClock) -> None:
    stats = LiveStats("enwiki", clock)
    # The stream can deliver an edit stamped a few seconds earlier than the one before it, or even in the future.
    stats.add(make_edit("A", at=WHEN))
    stats.add(make_edit("B", at=WHEN - timedelta(seconds=7)))
    stats.add(make_edit("C", at=WHEN + timedelta(hours=1)))
    assert stats.per_minute() == 3
    assert stats.series()[-1] == 3
    assert stats.total == 3


def test_flush_writes_per_page_counts_once_a_minute_and_prunes(clock: VirtualClock) -> None:
    store = Store(":memory:")
    stats = LiveStats("enwiki", clock)
    stats.add(make_edit("Ada", delta=100))
    stats.add(make_edit("Ada", delta=-30, bot=True))
    stats.add(make_edit("Talk:Ada", namespace=1))  # not an article: not stored
    minute = clock.now().strftime("%Y-%m-%dT%H:%M")
    assert stats.maybe_flush(store) is False  # a minute has not passed
    assert store.edit_minute("enwiki", "Ada", minute) is None
    clock.advance(60)
    assert stats.maybe_flush(store) is True
    assert store.edit_minute("enwiki", "Ada", minute) == (2, 1, 100, 30)
    assert store.edit_minute("enwiki", "Talk:Ada", minute) is None
    assert stats.maybe_flush(store) is False  # just flushed
    clock.advance(25 * 3600)
    stats.add(make_edit("Grace"))
    stats.flush(store)
    assert store.edit_minute("enwiki", "Ada", minute) is None  # older than 24 hours: pruned
    assert store.edit_minute("enwiki", "Grace", clock.now().strftime("%Y-%m-%dT%H:%M")) is not None


def test_without_a_store_nothing_is_kept_and_nothing_breaks(clock: VirtualClock) -> None:
    stats = LiveStats("enwiki", clock)
    stats.add(make_edit())
    clock.advance(120)
    assert stats.maybe_flush(None) is False
    stats.flush(None)
    assert stats.per_minute() == 0
