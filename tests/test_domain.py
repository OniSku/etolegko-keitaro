import pytest

from app.domain import Offer, Problem, distribute


def test_integer_allocation_is_stable_and_sums_to_one_hundred() -> None:
    for count in range(1, 151):
        offers = [Offer(offer_id=i + 1, name=str(i), share=0) for i in range(count)]
        allocated = distribute(offers)
        assert sum(x.share for x in allocated) == 100
        assert max(x.share for x in allocated) - min(x.share for x in allocated) <= 1
        assert [x.share for x in offers] == [0] * count
        reversed_result = {x.offer_id: x.share for x in distribute(list(reversed(offers)))}
        assert reversed_result == {x.offer_id: x.share for x in allocated}


def test_pinned_share_survives_and_removed_offers_get_zero() -> None:
    result = distribute(
        [
            Offer(offer_id=1, name="Pinned", share=25, pinned=True),
            Offer(offer_id=2, name="Other", share=0),
            Offer(offer_id=3, name="Other", share=0),
            Offer(offer_id=4, name="Archived", share=99, removed=True),
        ]
    )
    assert [x.share for x in result] == [25, 38, 37, 0]


@pytest.mark.parametrize(
    "offers",
    [
        [],
        [Offer(offer_id=1, name="Removed", share=0, removed=True)],
        [Offer(offer_id=1, name="Pinned", share=25, pinned=True)],
        [
            Offer(offer_id=1, name="P1", share=70, pinned=True),
            Offer(offer_id=2, name="P2", share=70, pinned=True),
        ],
    ],
)
def test_impossible_allocations_are_rejected(offers: list[Offer]) -> None:
    with pytest.raises(Problem):
        distribute(offers)
