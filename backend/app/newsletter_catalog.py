"""Curated newsletter source catalog supplied by the workspace operator.

The catalog is registry data only. Importing it never discovers a page, opens
a browser, submits a form, or starts a subscription. Every imported source is
kept in ``TEST_REQUIRED`` until discovery and a single-account test approve it.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit


CATALOG_GROUPS: tuple[tuple[str, str], ...] = (
    ("News & Public Affairs", "Newsrooms, public-interest reporting, politics, and civic information."),
    ("Technology & Business", "Technology, software, startups, marketing, finance, and professional work."),
    ("Science, Health & Education", "Science, medicine, wellbeing, research, and education sources."),
    ("Lifestyle, Travel & Culture", "Travel, food, entertainment, design, sport, and personal interests."),
    ("Shopping, Food & Brands", "Retail, consumer brands, offers, products, and commerce sources."),
)

# Raw entries are intentionally preserved as supplied. URL normalization below
# removes tracking queries and makes repeated imports idempotent.
CATALOG_SOURCES = """
whats-on-netflix.com
lesoleil.com
Fox Nation
internetisbeautiful.com
aireadycmo.com
anywhereitaly.com
strew.app
honeyletter.app
productprinciple.co
theglimpse.co
productparty.us
thediscourse.co
roame.travel
womenssurvivalguide.com
antonvolney.com
weeklydystopia.com
tickernerd.com
lifeisasacredtext.com
hobbycardlife.com
complexitycondensed.com
butcherbox.com
freerewardcenter.com
mycharityshopping.com
itdataleads.com
investors.com
firstround.com
designobserver.com
divenewsletters.com
animalz.co
futuretools.io
designmynight.com
contentdesign.london
themarshallproject.org
icij.org
muckrock.com
opensecrets.org
opendemocracy.net
insideclimatenews.org
yale.edu
ecowatch.com
statnews.com
retractionwatch.com
prismreports.org
platformer.news
current.org
streetsblog.org
tastecooking.com
nautil.us
dissentmagazine.org
democracydocket.com
forumvc.com
1000xiaoshibao.com
palazzofiuggi.com
hercampus.com
thesgnl.com
19thnews.org
harpers.org
inn.org
jstories.media
clal.it
alc.co.jp
nicepage.com
onesignal.com
plan-international.org
pragmaticpineapple.com
puck.news
wellcome.org
astm.org
beefeater.co.uk
compassion.com
djw.de
scrive.com
thedailyatom.com
theretrospectdaily.com
sfmc-content.com
growth.design
gumroad.com
marketingexamples.com
rubyweekly.com
javascriptweekly.com
avclub.com
Yardinflatables.com
Lampsplus.com
kottke.org
Fast-growing-trees.com
Revolve.com
Perfume.com
Ornamentmall.com
Blackphoenixalchemylab.com
Quadratec.com
oneplace.com
Allpopart.com
Zzounds.com
valentindecker.com
Hookedonhallmark.com
Topcubans.com
Bsnsports.com
Fnp.com
Cigars-of-cuba.com
Hammelmans.com
breakingcancernews.com
newsday.com
sudouest.fr
usbeketrica.com
brief.me
inserm.fr
marianne.net
telerama.fr
latribune.fr
lesechos.fr
radiofrance.fr
mediapart.fr
goop.com
verywellmind.com
stackoverflow.blog
marcopolo.me
devex.com
becomingminimalist.com
therecap.io
windsornewstoday.ca
vibetrace.com
summersalt.com
roka.com
shineon.com
local10.com
youfoodz.com
sakara.com
reservebar.com
shopvida.com
shapermint.com
openedition.org
idlo.int
osce.org
nato.int
wvi.org
government.se
garysguide.com
thisistrue.com
ruggable.com
atptour.com
britannica.com
udacity.com
toms.com
6pm.com
asus.com
techspot.com
lifewire.com
arstechnica.com
jstor.org
phys.org
explodingtopics.com
theinfatuation.com
select-home-warranty.com
refi-help-now.net
giftreward-center.com
nextjobhome.net
fabsurveycenter.com
harplendersdirect.com
engadget.com
thecouponmansion.com
highereducationseeker.com
greatsavingsfinder.com
couponclipclub.com
edu4careers.com
elitehomeshopping.com
youreducationsearch.com
summitdaily.com
bigvacationgiveaway.com
badcreditquoteking.com
90secondsinsurance.com
sony.fr
theflipside.io
inno.be
soumettre.fr
ninaramen.com
schoolhouse.com
cupshe.com
gravityblankets.com
peachandlily.com
qalo.com
blackriflecoffee.com
madeincookware.com
chubbiesshorts.com
byltbasics.com
leesa.com
improbable.com
musicjournalisminsider.com
www.ea.com
mailchi.mp
toysrus.com
old-silence-603.ck.page
bitter-feather-2551.ck.page
powwows.com
ithinkiwantadivorce.com
alyssapatmos.ck.page
unique-builder-1724.ck.page
masteringlaravel.io
thelittleleopard.ck.page
the-recipe-rebel.ck.page
marketrnotes.com
ecamm.com
vegankidsnutrition.ck.page
domesticsuperhero.com
themoneycouple.ck.page
her-packing-list.ck.page
two-peas-and-their-pod.ck.page
simplystacie.net
veggiessavetheday.com
moneyprodigy.com
part-timecreatorclub.ck.page
fpatuesday.ck.page
designed-decor.ck.page
brandmerry-by-michelle-knight.ck.page
creatorwizard.com
bijayspeaks.ck.page
fierce-architect-6942.ck.page
consumergiftcenter.us.com
kim24.pl
adameteve.fr
newsletterest.com
kiwico.com
1-day.co.nz
offers.com
universityworldnews.com
benjerry.com
francetelevisions.fr
volkswagenstiftung.de
pbs.org
peta.org
feederwatch.org
wienerzeitung.at
goldmansachs.com
fragrancenet.com
cartalk.com
imsa.com
byrdie.com
char.gd
democracygroup.org
thenextwave.blog
thepointsguy.com
csmonitor.com
allthesmallthings.co
cryptoclubnewsletter.com
theyetee.com
tableclothsfactory.com
getit.qa
hubblecontacts.com
satechi.net
tldr.tech
huntthealgos.substack
offersclub.com
americanresearchpanel.com
https://www.hellomagazine.com/newsletter/
https://www.theatlantic.com/newsletters/
https://www.bbc.com/newsletters?utm
https://mailchi.mp/theparisreview.org/newslettersignup
https://www.technologyreview.com/?utm_source=chatgpt.com
https://link.silive.com/join/6fm/signup
https://www.thedailybeast.com/newsletters/
https://www.sciencefriday.com/newsletters/
https://www.hayhouse.com/newsletters
https://marbles.substack.com
https://popular.info
https://frontendfoc.us
https://javascriptweekly.com
https://www.dailycodingproblem.com
https://tldr.tech/design
https://tldr.tech/webdev
https://tldr.tech/ai
https://tldr.tech/crypto
https://tldr.tech
https://www.bensbites.com
https://pythonweekly.com
https://www.superhuman.ai
https://futuretools.beehiiv.com
https://www.lennysnewsletter.com
https://www.notboring.co
https://www.exponentialview.co
https://www.thebatch.ai
https://every.to/subscribe?utm_source=every_website_hero
https://www.mindstream.news
https://www.aifire.co
https://www.earlybird.email
https://www.thediff.co
https://www.bigtechnology.com
https://www.marketingbrew.com/subscribe
https://aiforhumans.beehiiv.com/subscribe
https://www.mozilla.org/en-US/newsletter/developer/
https://devshot.co
https://zerotomastery.io/newsletters
https://weeklyfoo.com
https://www.ineeddev.com/products/newsletter
https://subscribe.engadget.com
https://www.shopify.com/newsletter
https://www.strategy-business.com/sb-newsletters
https://www.kaidavis.com/newsletter
https://hollyhockagency.com/join-the-party
https://www.vox.com/newsletters
"""


_TECH_BUSINESS = (
    "aireadycmo", "internetisbeautiful", "productprinciple", "productparty", "tickernerd", "investors.",
    "firstround", "animalz", "futuretools", "contentdesign", "forumvc", "onesignal", "scrive", "growth.design",
    "gumroad", "marketingexamples", "rubyweekly", "javascriptweekly", "stackoverflow", "devex", "vibetrace",
    "garysguide", "asus.", "techspot", "lifewire", "arstechnica", "explodingtopics", "engadget", "goldmansachs",
    "cryptoclub", "satechi", "tldr.tech", "dailycodingproblem", "bensbites", "pythonweekly", "superhuman.ai",
    "beehiiv", "lennysnewsletter", "notboring", "exponentialview", "thebatch", "every.to", "mindstream",
    "aifire", "earlybird", "thediff", "bigtechnology", "marketingbrew", "mozilla.org", "devshot", "zerotomastery",
    "weeklyfoo", "ineeddev", "shopify", "strategy-business", "kaidavis", "masteringlaravel", "marketrnotes",
    "creatorwizard", "bijayspeaks", "improbable", "platformer", "technologyreview", "frontendfoc.us",
)
_SCIENCE_HEALTH_EDUCATION = (
    "insideclimatenews", "yale.", "ecowatch", "statnews", "retractionwatch", "wellcome", "astm", "breakingcancer",
    "verywellmind", "inserm", "idlo.int", "britannica", "udacity", "jstor", "phys.org", "highereducation",
    "edu4careers", "youreducation", "universityworldnews", "volkswagenstiftung", "pbs.", "peta.", "feederwatch",
    "sciencefriday", "hayhouse", "schoolhouse", "vegankidsnutrition", "moneyprodigy", "openedition", "nato.int",
    "osce.org", "wvi.org", "government.se", "astm.org", "thebatch.ai", "realtime", "hubblecontacts",
)
_NEWS_PUBLIC_AFFAIRS = (
    "lesoleil", "thediscourse", "weeklydystopia", "complexitycondensed", "themarshallproject", "icij", "muckrock",
    "opensecrets", "opendemocracy", "prismreports", "current.org", "streetsblog", "dissentmagazine", "democracydocket",
    "thesgnl", "19thnews", "harpers", "inn.org", "jstories", "clal.it", "alc.co.jp", "puck.news", "theflipside",
    "newsday", "sudouest", "usbeketrica", "brief.me", "marianne", "telerama", "latribune", "lesechos", "radiofrance",
    "mediapart", "windsornewstoday", "local10", "thisistrue", "democracygroup", "wienerzeitung", "francetelevisions",
    "summitdaily", "csmonitor", "hellomagazine", "theatlantic", "bbc.com", "silive.com", "thedailybeast", "popular.info",
    "vox.com", "government.se", "nato.int", "osce.org", "powwows", "plan-international", "compassion.com",
)
_LIFESTYLE_CULTURE = (
    "whats-on-netflix", "anywhereitaly", "strew.", "honeyletter", "theglimpse", "roame.travel", "womenssurvivalguide",
    "antonvolney", "lifeisasacredtext", "hobbycardlife", "designmynight", "tastecooking", "nautil.us", "hercampus",
    "palazzofiuggi", "designobserver", "avclub", "kottke", "beefeater", "thedailyatom", "theretrospectdaily", "goop",
    "marcopolo", "becomingminimalist", "atptour", "cartalk", "imsa.com", "musicjournalisminsider", "ea.com", "powwows",
    "ithinkiwantadivorce", "ninaramen", "ecamm", "domesticsuperhero", "her-packing-list", "two-peas", "simplystacie",
    "veggiessavetheday", "fpatuesday", "designed-decor", "fierce-architect", "thenextwave", "thepointsguy", "byrdie",
    "theinfatuation", "the-recipe-rebel", "fast-growing-trees", "jamesclear", "allthesmallthings", "marbles.substack",
)


def _source_url(raw: str) -> str | None:
    value = raw.strip()
    if not value or " " in value and "://" not in value:
        return None
    candidate = value if "://" in value else f"https://{value}"
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or "." not in host or any(char.isspace() for char in host):
        return None
    path = parsed.path.rstrip("/")
    return f"https://{host}{path}"


def canonical_source_key(url: str) -> str:
    parsed = urlsplit(url)
    return f"{(parsed.hostname or '').lower().removeprefix('www.')}{parsed.path.rstrip('/') or '/'}"


def source_group_key(url: str) -> str:
    haystack = url.lower()
    if any(token in haystack for token in _NEWS_PUBLIC_AFFAIRS):
        return "news"
    if any(token in haystack for token in _SCIENCE_HEALTH_EDUCATION):
        return "science"
    if any(token in haystack for token in _TECH_BUSINESS):
        return "tech"
    if any(token in haystack for token in _LIFESTYLE_CULTURE):
        return "lifestyle"
    return "shopping"


def source_name(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").removeprefix("www.")
    label = re.sub(r"[-_.]+", " ", host).strip()
    path = parsed.path.strip("/").split("/")[-1] if parsed.path.strip("/") else ""
    if path and path.lower() not in {"subscribe", "newsletter", "newsletters", "join"}:
        label = f"{label} · {re.sub(r'[-_]+', ' ', path)}"
    return " ".join(word.capitalize() for word in label.split())[:160]


def catalog_entries() -> list[tuple[str, str, str]]:
    """Return unique ``(source_url, display_name, group_key)`` entries."""
    result: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for raw in CATALOG_SOURCES.splitlines():
        url = _source_url(raw)
        if not url:
            continue
        key = canonical_source_key(url)
        if key in seen:
            continue
        seen.add(key)
        result.append((url, source_name(url), source_group_key(url)))
    return result


CATALOG_SKIPPED_SOURCE_COUNT = sum(1 for raw in CATALOG_SOURCES.splitlines() if raw.strip() and _source_url(raw) is None)
