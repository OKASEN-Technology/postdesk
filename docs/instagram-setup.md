# Connecting Instagram

postdesk posts through Instagram's official API. Meta only opens that API
to **professional** accounts (Business or Creator) that are linked to a
**Facebook Page**, and only through a Meta developer app. Setting that up
is the one fiddly part of postdesk; it takes about twenty minutes, once.
Nothing here is specific to postdesk: it is Meta's "Instagram API with
Facebook Login" setup.

Screenshots are marked `TODO` with the step they belong to; the text is
complete without them.

## Before you start

- Your Instagram account is **professional**. In the Instagram app:
  Settings → Account type and tools → Switch to professional account.
  Either Business or Creator works.
- It is **linked to a Facebook Page** you manage. Instagram app: Settings →
  Accounts Center → Accounts → Add accounts → Facebook, and pick (or
  create) the Page. If you have no Page, create one at
  https://www.facebook.com/pages/create; it can be unpublished.

## 1. Create a Meta app

1. Go to https://developers.facebook.com/apps and sign in with the Facebook
   account that manages the Page.
2. **Create App**. For the use case pick **Other**, then the app type
   **Business**. Give it any name (it is never shown to your followers).
   `TODO screenshot: the app-type step with Business selected`
3. On the app's dashboard, under **Add products**, find **Instagram** and
   click **Set up**. Choose **API setup with Facebook login** (not the
   "Instagram login" variant: that one cannot see the Page).
   `TODO screenshot: the Instagram product tile and the two setup choices`
4. In the left menu open **App settings → Basic**. Copy **App ID** and
   **App secret** (click Show) into `.env`:

   ```
   META_APP_ID=1234567890
   META_APP_SECRET=abcdef...
   ```

   The app can stay in **Development** mode. Development mode only lets the
   app act for people with a role on it, and you are its admin, so that is
   all postdesk needs. You do not need App Review.

## 2. Generate a user token

1. Open the **Graph API Explorer**: https://developers.facebook.com/tools/explorer
2. Top right, **Meta App**: pick the app you just made.
3. **User or Page**: leave it on **User Token**.
4. **Permissions**: add these five. Type each into the box and pick it:

   | Permission | Why |
   |---|---|
   | `instagram_basic` | read your account's profile and media |
   | `instagram_content_publish` | publish posts and Stories |
   | `instagram_manage_insights` | the reach and interaction numbers |
   | `pages_show_list` | find the Page your account is linked to |
   | `pages_read_engagement` | read the Page (Meta requires it alongside the one above) |

   `TODO screenshot: the Explorer's permissions box with the five added`
5. Click **Generate Access Token**. Facebook opens a popup. **Tick both
   the Instagram account and the Page** when it asks which ones the app
   may access, then allow every permission.
   `TODO screenshot: the popup listing the Instagram account and the Page`
6. Copy the token into `.env`:

   ```
   INSTAGRAM_ACCESS_TOKEN=EAAB...
   ```

   This token lasts about an hour. The next step turns it into one that
   does not expire, so do the next step now.

## 3. Swap it for a Page token

```bash
docker compose exec app postdesk instagram page-token
```

(Without Docker: `postdesk instagram page-token` in the folder with `.env`.)

It extends the user token to 60 days, finds the Page linked to your
Instagram account, and prints two lines:

```
INSTAGRAM_PAGE_TOKEN=EAAB...
INSTAGRAM_USER_ID=17841400000000000
```

Paste both into `.env` and restart (`docker compose up -d`). A Page token
obtained this way **does not expire**; the user token from step 2 is no
longer needed and can be deleted from `.env`.

If it says the token sees no Page with a linked Instagram account, go back
to step 2.5 and make sure both the Page and the Instagram account were
ticked in the popup. If it says the token reaches several accounts, set
`INSTAGRAM_USER_ID` to the one you want (the ids are in the message) and
run it again.

## 4. Check

```bash
docker compose exec app postdesk instagram whoami
```

```
account:  @yourhandle (17841400000000000)
token:    INSTAGRAM_PAGE_TOKEN, expires never
quota:    0 of 100 posts used in the last 24 hours
```

Instagram allows 100 published posts per account per rolling 24 hours;
`whoami` shows where you stand. `postdesk doctor` runs the same check.

## Later

- **The token stops working.** Changing the Facebook password, removing
  the app from the account, or switching the Instagram account's Page
  invalidates it. Repeat steps 2 and 3.
- **"Media not fetchable" when publishing.** Instagram could not download a
  slide from your public URL. See docs/deploy.md on making the app
  reachable or using a bucket; `postdesk doctor` tests it.
- **Going live / App Review.** Not needed while only you (an admin of the
  app) use it. If other people's accounts should post through your
  install, that is Meta's App Review process, outside this guide.
