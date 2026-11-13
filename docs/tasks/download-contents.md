# Download Contents

- Downloads character cards from [db.bepis.moe](https://db.bepis.moe), [koikatsucards.com](https://koikatsucards.com), [pixiv.net](https://www.pixiv.net), [pawchive.pw](https://pawchive.pw), [gofile.io](https://gofile.io), [catbox.moe](https://catbox.moe), [mediafire.com](https://www.mediafire.com), [pixeldrain.com](https://pixeldrain.com), [terabox.com](https://www.terabox.com), [dropbox.com](https://www.dropbox.com), [mega.nz](https://mega.nz) and [Google Drive](https://drive.google.com).
- **Output Directory** defaults to `C:/KKAFIO/Downloads`.
- Enter one URL per line in the Download Links field. Supports individual card pages and listing pages.
- **Pagination formats** (use `|` as separator):

  | Format                                      | Behaviour                        |
  | ------------------------------------------- | -------------------------------- |
  | `https://db.bepis.moe/user/cards`           | Single listing page or card page |
  | <code>https://db.bepis.moe/user/cards &#124; all</code>    | All pages until empty            |
  | <code>https://db.bepis.moe/user/cards &#124; 3</code>      | Only page 3                      |
  | <code>https://db.bepis.moe/user/cards &#124; 1 &#124; 5</code> | Pages 1 through 5                |
  | <code>https://db.bepis.moe/user/cards &#124; 5 &#124; 1</code> | Pages 5 down to 1 (reverse)      |

- `link | page` fetches just that one page. If the link already has a `page` in it (e.g. `...?page=2`), it is replaced by the number you give, and any other filters in the link (tags, sort, etc.) are kept.
- Lines starting with `#` are treated as comments and ignored.
- **Report:** a `kkafio_download_contents_report.txt` file is written to the output folder every time this task runs (replacing the previous one), listing every link with how many files were downloaded, skipped and failed, the links that failed and why, and — if you stopped the run — the links that were not reached.
- **Open Report** _(on by default)_ — automatically opens the report in your default text editor when any download failed. Turn off to never open it automatically (the report is still written).
- **Skip already downloaded** (on by default) uses a history file at `%APPDATA%/KKAFIO/download_history.json` to avoid re-downloading files.
  - **Tip:** combined with **Skip already downloaded**, a paginated link effectively lets you "subscribe" to an author or series — re-running the same task later only picks up whatever's new. For example, on koikatsucards.com you can grab everything from an author or series with `| all` once, then switch to a small window like `| 1 | 3` for future runs (widen it if they upload less often than that) so each run only checks the first few pages, skips anything already downloaded, and grabs whatever's new.

## Source-specific notes

### koikatsucards.com session cookie

downloading from koikatsucards.com requires a valid session. KKAFIO manages this automatically:

- On first use (or when the session expires), KKAFIO opens [koikatsucards.com/login](https://koikatsucards.com/login) in your browser and shows a dialog asking you to paste the `kkd_session` cookie value from DevTools (F12 → Application → Storage → Cookies → koikatsucards.com in Chrome/Edge; Storage → Cookies → koikatsucards.com in Firefox).
- The session is validated against `koikatsucards.com/api/session` before use. If it is expired, you are prompted for a new one automatically.
- The session is stored in `%APPDATA%/KKAFIO/sessions/koikatsucards.json`. It does not need to be entered in the GUI settings.

### pixiv.net

accepts `https://www.pixiv.net/en/artworks/ID`, `https://www.pixiv.net/en/users/ID/illustrations` and `https://www.pixiv.net/en/users/ID` (a bare user link is read as `users/ID/artworks`, i.e. illustrations + manga; `/manga` is also accepted). Galleries are paged like the site (48 works per page, newest first): `| 2`, `| 1 | 3`, `| 3 | 1` (reverse) and `| all` work as above, and a link that already ends in `?p=2` (e.g. `.../users/85712366/illustrations?p=2`) reads just that page. With neither, the whole gallery is read. Combined with **Skip already downloaded**, a small window such as `| 1 | 2` lets you pick up only new works on later runs. Page boundaries follow work order (newest first), which matches pixiv's own pages unless works were recently added or removed.

- **Tag galleries** — a user's illustrations filtered by one of their tags, e.g. `https://www.pixiv.net/en/users/3986523/illustrations/キャラ配布(コイカツ)` (the percent-encoded form pasted from the browser works too; `/artworks/TAG` and `/manga/TAG` as well), reads only the works with that tag. Page directives, `?p=N` and `| f` work as for any gallery.
- **Search links** — paste a pixiv search URL as-is, e.g. `https://www.pixiv.net/search?q=...&s_mode=tag&type=artwork&ai_type=1&r=1` (`/en/tags/KEYWORD/artworks` tag pages work too). `type=artwork`, `illust` and `manga` are supported. Every other query parameter (`s_mode`, `mode`, `order`, `ai_type`, date / size filters, ...) is passed through to pixiv unchanged, so the same filters as in the browser apply. Results are paged (60 per page): `| N`, `| N | M` and `| all` pick pages, a `?p=N` in the link reads just that page, and **without any of them only one page is read** (a search can be huge, so use `| all` to go through everything). Search results already include each work's size, so single-image works that fail the size filter are dropped without any further request.
- **Description links** — the mega.nz, Google Drive, gofile.io, catbox.moe, mediafire.com and pixeldrain.com links in an artwork's description are always downloaded along with its images (this costs one extra request per artwork; links repeated in several descriptions are downloaded once).
- **Follow illustrations** — add `| f` to a pixiv line (`| fi`, `| follow` and `| follow illustrations` work too) to also download the other pixiv artworks mentioned in the description (`https://www.pixiv.net/en/artworks/ID` or pixiv's `illust/ID`). Their images are downloaded, and their descriptions are read the same way (mega / Google Drive / gofile links, and further artworks), and so on. Every artwork is visited only once per run, so artworks that mention each other cannot loop. Works on artworks, galleries and search links, and combines with page directives, e.g. `https://www.pixiv.net/en/users/ID | 1 | 3 | f`. Linked artworks still go through the size filter (only matching PNGs are kept).
- **Only matching images are downloaded:** exactly 320x180 (scene thumbnails) or any size with the 252x352 aspect ratio (character / coordinate cards). The size is read from pixiv's API before downloading, so everything else is skipped without being fetched.
- **PHPSESSID cookie** — like koikatsucards.com, KKAFIO opens [accounts.pixiv.net/login](https://accounts.pixiv.net/login) and shows a dialog asking for the `PHPSESSID` cookie value (DevTools → Application/Storage → Cookies → https://www.pixiv.net). It is stored in `%APPDATA%/KKAFIO/sessions/pixiv.json`; the `PIXIV_PHPSESSID` environment variable also works. It is only needed for R-18 / restricted works: cancelling the dialog continues without a login. To enter a new one, delete `pixiv.json`.

### pawchive.pw

accepts three kinds of link, each converted to the matching API endpoint (`/api/v1/...`):

| Link                                                   | Reads                           |
| ------------------------------------------------------ | ------------------------------- |
| `https://pawchive.pw/posts` (`?q=...` for a search)    | the posts listing / search      |
| `https://pawchive.pw/patreon/user/58585170`            | all posts of a creator          |
| `https://pawchive.pw/patreon/user/58585170/post/173280929` | one post                    |

- **Where files come from:** the public file host (`file.pawchive.pw/data/...?f=<name>`) is tried first and the storage node that holds the file (`n<node>.pawchive.pw`) only if that fails — the node host on its own tends to answer `403 Forbidden`. Requests identify themselves as `KKAFIO/<version>` with a pawchive `Referer`, over HTTP/1.1.
- **Oversized files (`t1.pawchive.pw`):** attachments Pawchive keeps in its 30-day temporary storage have no path in the API; their signed links are read from the post's web page. Downloading them needs a logged-in account, so — like the pixiv `PHPSESSID` — KKAFIO opens [pawchive.pw/account/login](https://pawchive.pw/account/login) and shows a dialog asking for the `session` cookie value (DevTools → Application/Storage → Cookies → https://pawchive.pw). It is asked for **only when such a file turns up**, at most once per run, and stored in `%APPDATA%/KKAFIO/sessions/pawchive.json`; the `PAWCHIVE_SESSION` environment variable also works. A stored cookie is checked against pawchive.pw and asked for again when it has expired. Cancelling the dialog continues without a login: the temporary files are then reported as failed, everything else downloads normally. To enter a new one, delete `pawchive.json`.
- **What is downloaded from each post:** its main file and every attachment, plus every **mega.nz**, **gofile.io**, **catbox.moe**, **mediafire.com**, **pixeldrain.com** and **Google Drive** link found in the post text (handled exactly like pasting that link yourself, so folders, `#KEY`s and `?password=` work). Links are used exactly as written in the post: query parameters (`?password=...`, `?usp=sharing`, ...) and the `#KEY` of mega links are kept.
- **PNGs are filtered:** a `.png` file or attachment is only kept when it is exactly 320x180 or has the 252x352 aspect ratio (the same rule as pixiv). The size is read from the first bytes of the file with [imagesize](https://pypi.org/project/imagesize/), so a non-matching PNG is abandoned after its first chunk instead of being downloaded in full. Every other file type (archives, ...) is downloaded as is.
- **Paging:** both listings are paged 50 posts at a time (`?o=`; page N is `o=(N-1)*50`). `| N`, `| N | M` (either direction) and `| all` pick pages like the other sources, a `?o=100` in the link reads just that page, and **without any of them only page 1 is read** (use `| all` for everything). Every other query parameter of the link (search text, tags, ...) is passed to the API unchanged. A single post ignores the page directive.
- Files keep their original names; when two posts use the same name (e.g. `000.png`) the later one gets the start of its content hash added, so nothing is overwritten. A file or link that appears in several posts is only downloaded once.

### gofile.io

paste a folder link such as `https://gofile.io/d/AbC123`. Every file in the folder is downloaded; nested folders are downloaded into subfolders of the output directory named after them, so same-named files never overwrite each other. No login is needed: like megaloader, KKAFIO creates a throw-away guest account for each run (set the `GOFILE_TOKEN` environment variable to use your own account token instead). For a password-protected folder append `?password=...` to the link. The page directive is ignored.

- Gofile signs its API requests with a website token that includes a secret which gofile changes every few months. KKAFIO reads the current secret from gofile's own script (the same method as HeapLeach), so rotations are picked up automatically. If that ever fails, set the `GOFILE_SALT` environment variable to the current secret. After a rejected signature (`error-notPremium`) KKAFIO stops sending signed requests for 15 minutes (this also survives restarting the app), because gofile blocks addresses that keep signing badly.

### catbox.moe

paste a file link (`https://files.catbox.moe/abc123.png`, or a Litterbox one on `litter.catbox.moe`) or an album link (`https://catbox.moe/c/abc123`). An album's files are read from its page and every one is downloaded, whatever its type, into the output directory. No login is needed.


### mediafire.com

paste a file link (`https://www.mediafire.com/file/ID/name.zip/file`; the `/view/`, `/download/` and old `mediafire.com/?ID` shapes work too), a folder link (`https://www.mediafire.com/folder/ID/name`) or a direct `downloadNNNN.mediafire.com` link. MediaFire has no download API for files, so KKAFIO reads the real download link from the file's page. A folder is listed with MediaFire's public folder API: every file is downloaded, and nested folders become subfolders of the output directory named after them. No login is needed. Files MediaFire has removed or blocked (for a virus or a copyright claim) have no download link and are reported as failed.

### pixeldrain.com

paste a file link (`https://pixeldrain.com/u/AbC123`) or a list link (`https://pixeldrain.com/l/AbC123`); the `pixeldrain.net` and `pixeldrain.dev` mirrors work too. Every file of a list is downloaded into the output directory (a `#item=N` on a list link is ignored). No login is needed. Pixeldrain can refuse anonymous downloads of very popular files (rate limit or captcha required), which shows up as a failed download.

### terabox.com

paste a share link (`https://www.terabox.com/s/1AbC123`, `https://www.terabox.com/sharing/link?surl=AbC123`, or the same on any TeraBox mirror such as `terabox.app`, `1024terabox.com`, `1024tera.com`, `teraboxapp.com` or `nephobox.com`). Every file of the share is downloaded into the output directory; nested folders become subfolders named after them. To take only one folder of a share, keep the `path=` (or `dir=`) part of the link, e.g. `https://www.terabox.com/sharing/link?surl=AbC123&path=%2FMods`. To take **one file**, give its full path the same way, e.g. `https://www.terabox.com/sharing/link?surl=AbC123&path=%2FMods%2Fhair.zipmod` (TeraBox has no link of its own for a file; one line per file). The page directive is ignored. The resolver is the one of [Teralace](https://github.com/LoLyeah/teralace) (without its user-configured API pool / Cloudflare Worker resolvers):

- **Listing** — read from the share's mobile page, which also carries the share's signature and a ready download link per file; items beyond the first 20 and the content of subfolders come from TeraBox's `/share/list` API, page by page (folders up to 5 levels deep).
- **Download links** — a fresh link is requested from TeraBox's `sharedownload` API (a link the CDN refuses with 403 is dropped); if that fails, the third-party `indexsubtitle` resolver is tried (it works for many files without a login; it is sent the share link and file id), and finally the link that came with the listing.
- **`ndus` cookie** — most original files need a TeraBox account. Like the pixiv `PHPSESSID`, KKAFIO opens [terabox.com/login](https://www.terabox.com/login) and shows a dialog asking for the `ndus` cookie value (DevTools → Application/Storage → Cookies → https://www.terabox.com). It is asked for only when the list holds a TeraBox link, and stored in `%APPDATA%/KKAFIO/sessions/terabox.json`; the `TERABOX_NDUS` environment variable also works. TeraBox has no cheap "am I logged in?" check, so the value is only checked for its shape. Cancelling the dialog continues without a login: files that need an account are then reported as failed, the others download normally. To enter a new one, delete `terabox.json`.
- Already downloaded files are remembered by share and file id (TeraBox's download links are temporary), so **Skip already downloaded** works across runs.

### dropbox.com

paste a shared file link (`https://www.dropbox.com/scl/fi/ID/name.zip?rlkey=KEY&dl=0`, or the old `https://www.dropbox.com/s/ID/name.zip?dl=0`), a shared folder link (`https://www.dropbox.com/scl/fo/ID/KEY?rlkey=KEY&dl=0` or the old `/sh/ID/KEY`) or a direct `dl.dropboxusercontent.com` link. KKAFIO asks Dropbox for the link with `dl=1` (other parameters such as `rlkey` are kept, as new style links do not work without it). A folder is served by Dropbox as one zip, which KKAFIO unpacks into the output directory, keeping its subfolders. No login is needed. Links the owner has removed, or files that hit Dropbox's traffic limit, show up as failed downloads. The page directive is ignored.

### mega.nz

paste a file link (`https://mega.nz/file/ID#KEY`), a folder link (`https://mega.nz/folder/ID#KEY`) or a single file of a folder (`.../folder/ID#KEY/file/NODE`); the old `#!ID!KEY` / `#F!ID!KEY` shapes work too. The part after `#` is the decryption key and is required: Mega encrypts names and data, and KKAFIO decrypts them while downloading. Folders keep their structure (nested folders become subfolders). Mega limits free transfer per IP address; if the quota is used up the file fails with the wait time Mega reports, and a re-run later resumes (finished files are skipped). The page directive is ignored.


### Google Drive

public files (`https://drive.google.com/file/d/ID/view`, `open?id=ID`, `uc?id=ID`) and folders (`https://drive.google.com/drive/folders/ID`); subfolders become subfolders. Items must be shared as "anyone with the link". Large files go through Google's confirmation page automatically; if a file has hit Google's download quota you get that reason and can retry in about a day. Google Docs/Sheets/Slides inside a folder are skipped (they aren't files). The page directive is ignored.
