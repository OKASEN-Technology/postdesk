"""
The board (docs/roadmap.md, M2): one card per post, moved through the
columns by the scout, the agents, the publisher and you. `cards` holds the
columns and the gates, `transitions` the moves, `scout` what is new,
`sync` the queue and the board in step, `posts` a card's drafts, `facts`
what a card knows.
"""

from postdesk.board.cards import COLUMNS, GATES, HUMANS, BoardError, GateRefused  # noqa: F401
