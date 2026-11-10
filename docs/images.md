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

In Settings → Voice, set **Public brand assets URL** to the public HTTPS
folder containing a `postdesk-brand.json` index. Public object storage does
not normally list files at the folder URL, so the index names the images the
partner can consider. For example:

```json
{"images": [
  {"path": "helmet.jpg", "description": "Our head-worn device"},
  {"path": "team/workshop.png", "description": "Our workshop"}
]}
```

Paths are relative to the public folder and must stay within it. List at most
12 publicly accessible images. The partner sees the images and their descriptions
as background, and may select them for a brief. Review the brief before use;
adding an image affirms you have the right to use it. Only place files in the
index that you are comfortable sharing with the selected AI provider. A
Cloudflare dashboard link is not a public folder URL.

