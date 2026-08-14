"""Parsing a pasted draft board (S76, S10B).

The input is a paste from a results page, not an export, so it arrives malformed
in ways an export never is. Nearly every test here is about the parser refusing:
a board missing picks parses cleanly, looks complete, and is wrong about who was
available at every pick after the gap -- and nothing downstream can tell.
"""

import pytest

from pipeline.ingest import draft_log


def _board(teams: int, rounds: int, fmt: str = "{overall}. Player{overall} RB ATL") -> str:
    return "\n".join(fmt.format(overall=n) for n in range(1, teams * rounds + 1))


# -- the shapes people actually paste --------------------------------------


@pytest.mark.parametrize(
    "line, player, position, team",
    [
        ("1.01 Bijan Robinson RB ATL", "Bijan Robinson", "RB", "ATL"),
        ("1.01 Bijan Robinson", "Bijan Robinson", None, None),
        ("Pick 1 - Bijan Robinson, RB, ATL", "Bijan Robinson", "RB", "ATL"),
        ("1 – Bijan Robinson", "Bijan Robinson", None, None),
        ("1\tBijan Robinson\tRB\tATL", "Bijan Robinson", "RB", "ATL"),
        ("1,Bijan Robinson,RB,ATL", "Bijan Robinson", "RB", "ATL"),
        ("Bijan Robinson", "Bijan Robinson", None, None),
    ],
)
def test_the_common_result_shapes_all_parse(line, player, position, team):
    row = draft_log.parse_lines(line)[0]
    assert row["source_player_name"] == player
    assert row["position"] == position
    assert row["team"] == team


def test_names_with_punctuation_survive():
    """De'Von Achane, A.J. Brown, Marvin Harrison Jr. are all real players."""
    text = "1.01 De'Von Achane RB MIA\n1.02 A.J. Brown WR PHI\n1.03 Michael Pittman Jr. WR IND"
    names = [r["source_player_name"] for r in draft_log.parse_lines(text)]
    assert names == ["De'Von Achane", "A.J. Brown", "Michael Pittman Jr."]


def test_page_furniture_is_skipped_but_a_broken_pick_is_not():
    """A header row is noise; a line that meant to be a pick is a lost pick."""
    rows = draft_log.parse_lines("Round 1\nPick\tPlayer\tPos\n1.01 Bijan Robinson RB ATL")
    assert len(rows) == 1

    with pytest.raises(draft_log.DraftLogError, match="line 2"):
        draft_log.parse_lines("1.01 Bijan Robinson RB ATL\n>>> 1.02 ???  <<<")


# -- the refusals ----------------------------------------------------------


def test_a_short_board_is_refused_rather_than_returned():
    """The failure this module exists for: a paste that lost a few lines."""
    text = _board(12, 3)
    text = "\n".join(text.split("\n")[:-2])          # two picks short of 3 rounds
    with pytest.raises(draft_log.DraftLogError, match="parsed 34 picks"):
        draft_log.parse(text, teams=12, rounds=3)


def test_a_genuinely_short_draft_can_be_recorded_explicitly():
    rows = draft_log.parse(_board(12, 2), teams=12, rounds=3, partial=True)
    assert len(rows) == 24


def test_a_duplicated_pick_number_is_refused():
    text = "Pick 1 - A\nPick 2 - B\nPick 2 - C\nPick 4 - D"
    with pytest.raises(draft_log.DraftLogError, match="duplicated"):
        draft_log.parse(text, teams=2, rounds=2)


def test_a_gap_in_the_pick_numbers_is_refused():
    text = "Pick 1 - A\nPick 2 - B\nPick 4 - C\nPick 5 - D"
    with pytest.raises(draft_log.DraftLogError, match="missing"):
        draft_log.parse(text, teams=2, rounds=2)


def test_an_empty_paste_is_an_error_not_an_empty_draft():
    with pytest.raises(draft_log.DraftLogError, match="no picks"):
        draft_log.parse("\n  \n", teams=12)


# -- the snake, which is what makes the log checkable against the sheet -----


def test_the_seat_is_derived_by_snaking_back_on_even_rounds():
    rows = draft_log.parse(_board(12, 3), teams=12)
    by_pick = {r["overall_pick"]: r for r in rows}
    assert by_pick[1]["slot"] == 1 and by_pick[1]["round"] == 1
    assert by_pick[12]["slot"] == 12
    assert by_pick[13]["slot"] == 12 and by_pick[13]["round"] == 2   # snake turns
    assert by_pick[24]["slot"] == 1
    assert by_pick[25]["slot"] == 1 and by_pick[25]["round"] == 3


def test_the_derived_seats_are_the_inverse_of_held_picks():
    """The sheet says which picks a seat holds; the log says which seat made a
    pick. If these two disagree the audit trail compares the wrong rows."""
    from research.foundations.survival import held_picks

    rows = draft_log.parse(_board(12, 5), teams=12)
    by_pick = {r["overall_pick"]: r["slot"] for r in rows}
    for slot in range(1, 13):
        for pick in held_picks(12, slot, rounds=5):
            assert by_pick[pick] == slot


def test_round_pick_shapes_are_numbered_by_position_not_by_their_own_label():
    """`1.01` states a round and a seat, not an overall pick, and resolving it
    needs the team count -- which the line does not carry."""
    rows = draft_log.parse(_board(10, 2), teams=10)
    assert [r["overall_pick"] for r in rows] == list(range(1, 21))
    assert rows[10]["round"] == 2


# -- the suffix trap -------------------------------------------------------


@pytest.mark.parametrize(
    "line, player, team",
    [
        ("1. Kenneth Walker III RB SEA", "Kenneth Walker III", "SEA"),
        ("1. Kenneth Walker III", "Kenneth Walker III", None),
        ("1. Marvin Harrison Jr. WR ARI", "Marvin Harrison Jr.", "ARI"),
        ("1. Brian Robinson Jr.", "Brian Robinson Jr.", None),
    ],
)
def test_a_generational_suffix_is_not_mistaken_for_a_team(line, player, team):
    """III is three capitals and is not a franchise."""
    row = draft_log.parse_lines(line)[0]
    assert row["source_player_name"] == player
    assert row["team"] == team


def test_space_separated_position_and_team_are_split_off_the_name():
    """Otherwise the crosswalk is asked to match 'Bijan Robinson RB ATL'."""
    row = draft_log.parse_lines("7. Bijan Robinson RB ATL")[0]
    assert row["source_player_name"] == "Bijan Robinson"
    assert (row["position"], row["team"]) == ("RB", "ATL")
