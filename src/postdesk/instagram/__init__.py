"""Instagram through the Graph API: the client (`graph`), publishing
(`publish`) and insights (`insights`). The names most callers want are
re-exported here, so `from postdesk import instagram as ig` reads like the
module it was."""

from postdesk.instagram.graph import (  # noqa: F401
    Account,
    Graph,
    InstagramError,
    exchange_for_long_lived,
    publishing_limit,
    resolve_account,
    token_info,
)
from postdesk.instagram.publish import (  # noqa: F401
    CAPTION_MAX,
    CAROUSEL_MAX,
    CAROUSEL_MIN,
    HASHTAG_MAX,
    IMAGE_MAX_BYTES,
    Post,
    Slide,
    build_post,
    check_public,
    hashtags,
    lint_caption,
    parse_caption_file,
    publish_hosted,
    to_jpeg,
    to_jpeg_bytes,
    verify_hosted,
)
