# Images

Every picture on a slide has a credit and a provenance record: where it
came from, the page it was found on, its licence (or that it has none),
its author, and who picked it. The record is the stored file's
`content_asset.provenance`; the slide shows the credit, the caption tags
the account when there is one, and the editor's **rights** check reads the
record before you see the post.

`[images].sources` in `postdesk.toml` (or **Settings -> Images**) says
where a picture may come from:

```toml
[images]
sources = ["page", "wikimedia", "business_discovery", "attachment"]
```

Leave a source out and nothing uses it: the partner is told it is off, and
a photo you send is refused if `attachment` is off.

## The four sources

| Source | What it finds | Credit | Licence |
|---|---|---|---|
| `page` | the picture on the source's own page: its `og:image` (or `twitter:image`, `image_src`), else the first large image in the article. Only on the card's own site or `[partner].allowed_domains`. | `Image: <site name>` | none: used on the site's terms, at your risk |
| `wikimedia` | a search of Wikimedia Commons. Only public domain, CC0 and CC BY (any version); share-alike, non-commercial, no-derivatives and anything unlabelled are skipped. | `Photo: <author> / Wikimedia Commons, CC BY 4.0` | the file's own |
| `business_discovery` | a recent post on the subject's own Instagram account, through the Graph API's Business Discovery (your own token; never scraping). A post on someone else's account (a venue, a shop) is offered only if its caption names the subject. A small Claude model (`[llm].pick`) picks the one that shows the subject or their work, not a flyer or a selfie, and may pick none. | `Photo: @handle on Instagram`, and the caption tags them | none: their picture, used with credit, at your risk |
| `attachment` | a photo you attach to a card: on the card page, or in Telegram as a photo sent in reply to the card's message, with where it is from in the caption. | what the partner takes from your note, e.g. `Photo: Jo Baker` | what your note says, else none |

A feed's own picture (`rss`) and a link you file (`/new <link>`, or a link
on the board) use the page's picture the same way as `page`.

## How a picture is chosen

1. The partner reads the source and the pictures it already has (the
   feed's, the page's, any you attached).
2. If the post needs one it does not have, its reply asks for lookups:
   `{source, subject, query}`, at most six a card. A Wikimedia query is a
   search; a Business Discovery query is a handle; a page query is an
   address on the card's own site.
3. Each lookup runs after the partner's turn. What comes back is
   downloaded, stored with its provenance, and filed on the card as
   `found-1`, `found-2`... A note in the card's thread lists each with its
   credit and licence, and what failed and why.
4. The partner gets another turn, sees the finds as images, and picks.
   The brief (on the board and in Telegram) shows the chosen picture with
   its credit and licence.

Nothing is generated: postdesk does not make up pictures.

## Permissions on file

A credit is not a licence. When a rights holder says yes (a baker, a shop,
a photographer), put their yes on file:

```bash
postdesk permission add --holder "Jo Baker" --instagram jo.bakes \
  --credit "Photo: Jo Baker" --tag "@jo.bakes" \
  --scope "feed posts and Stories, any of her photos" --evidence "DM of 2 March"
```

or **Settings -> Image permissions -> Record one**. A permission names the
holder's Instagram handle or web domain (so their pictures can be
matched), what they agreed to, and where the yes is. It can be limited to
one subject and can expire. From then on a picture from that account or
site carries the permission's id in its record and the credit and tag the
holder asked for.

Only a person records a permission; an agent cannot. `postdesk permission
list` shows them and `postdesk permission revoke <id>` ends one (the
record stays, marked revoked).

## A source of your own

An image source is a class with `key` and
`find(session, request, ctx) -> list[Found]`, registered on the
`postdesk.images` entry point:

```toml
# your package's pyproject.toml
[project.entry-points."postdesk.images"]
museum = "my_package.images:MuseumImages"
```

`Found` (in `postdesk.images`) carries `url`, `source`, `credit`,
`permalink`, `licence`, `licence_url`, `author`, `permission_id`, `tag`,
`what` and `subject`. Return only pictures a post may use on the terms you
record; postdesk stores and credits whatever you return. Add its key to
`[images].sources` to turn it on. (The partner's lookups use the three
built-in ones; a plugin source is offered from its own code paths.)
# Brand assets for briefs

Connect a public Cloudflare R2 folder under Settings → Brand media library.
Use a read-only key scoped to that bucket, and enter the public URL of the
folder corresponding to the prefix. The app encrypts the credentials with
`POSTDESK_CREDENTIALS_KEY` and never returns them to the browser. Public
folder URLs serve files but cannot list them, so the read-only key is needed
for discovery. A dashboard link is not a public folder URL.

Postdesk lists the bucket automatically and lets an administrator exclude
individual files. New files are enabled by default. During a partner turn it
selects up to eight enabled JPEG, PNG or WebP images using filename hints. It
also attempts to sample up to three frames from one enabled video of at most
30 MB using ffmpeg. Filenames are hints rather than verified descriptions;
frames do not represent the whole video or its audio. These images and frames
are sent to the selected AI provider. GIF and AVIF files appear in the
library, but are not sent as images to the model. Adding files affirms the
account has the right to use and share them. A sample of a large library is
shown to the AI on any one turn, not all files at once.

The library can include files you do not intend to publish, so review its
selection. Existing `postdesk-brand.json` configuration still works when no
R2 connection is configured, for compatibility.

# Reels

From New post, choose Create an Instagram Reel and select an enabled brand
video or upload an MP4. Postdesk supports files up to 50 MB, H.264 or HEVC
video and AAC audio. It checks duration and width with ffprobe; Instagram
performs additional validation when processing the video. The image and video
tools are included in the Docker app image. Review the playable draft and
approve it separately before the publisher can send it. Reels cannot currently
be cross-posted to X. A separate scheduler must be running to publish approved
posts; queueing a draft does not publish it.
