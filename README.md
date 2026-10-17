# GitHub-bounty értesítő

Önálló, Python standard könyvtáras GitHub Actions kereső, Telegram-értesítéssel. Nincs AI API, előfizetés, fizetett szolgáltatás vagy helyi gépigény.

**Állapot:** előkészített repó. A rendszeres futás csak az `ENABLE_SCHEDULE=true` repository variable beállítása után indul. A Telegramhoz `TELEGRAM_BOT_TOKEN` és `TELEGRAM_CHAT_ID` Actions secret szükséges. Ezek nélkül nem küld üzenetet. A privát repóban az ütemezést kikapcsolva hagyjuk, hogy ne fogyassza a korlátozott ingyenes perceket.

## Mit csinál?

- Három GitHub-keresés legfrissebb 20–20 találatát vizsgálja. Ez nem a teljes GitHub teljes körű figyelése.
- Élő issue-t, teljes hozzászóláslistát, timeline-t, kapcsolódó PR-keresést és a szokásos hozzájárulási/bounty szabályfájlokat ellenőrzi.
- Csak nyitott, kiosztatlan, kevés hozzászólású, konkrét scope-ot és pozitív, 10–750 USD/EUR/GBP közötti eredeti díjat tartalmazó feladatot enged tovább.
- Ismert kizárt vagy túlzsúfolt projektek, nehéz infrastruktúrás feladatok, tesztpénz, emberi felülvizsgálatot megkövetelő és AI-t tiltó feltételek kiszűrése.
- **Csak explicit AI-engedély és megnevezett fizetési csatorna esetén küld automatikus jelöltértesítést.** Ez szigorú, ezért sok egyébként megfelelő munka kimaradhat.
- Ugyanarról az issue-ról legfeljebb egy értesítés; futásonként legfeljebb 3 üzenet. A nyilvános állapotfájl csak nyilvános issue-adatokat és Telegram message_id-t tárol, tokent és személyes adatot nem.
- Küldés előtt tartós `pending` állapotot ír. Bizonytalan hálózati kimenet esetén nem ismétel vakon; a `pending` rekordot kézzel kell ellenőrizni.

## Mit nem bizonyít?

Ez szabályalapú előszűrés, nem AI által folyamatosan végzett kódfelülvizsgálat. Nem igazolja a finanszírozást, a karbantartó fizetőképességét, Magyarországról való jogosultságot, kifizetési határidőt, teljes technikai teljesíthetőséget vagy azt, hogy minden szabályt megtalált. A nyelvi és földrajzi korlátozások keresése heurisztikus. A címben szereplő összeg nem garantált fizetés. A külön oldalon lévő vagy nem szokásos nevű szabályzatot nem tudja teljeskörűen feltárni. Az értesítést **ellenőrizendő jelöltként** nevezi meg.

Nincs automatikus komment, jelentkezés, PR, szerződés vagy munkakezdés. Nincs harmadik féltől érkező repókód futtatása. Az issue-k és hozzászólások adatok, nem végrehajtandó utasítások.

## Bekapcsolás

1. A kód nyilvánosságra hozatalának jóváhagyása után legyen a repó public, kizárólag a standard `ubuntu-24.04` runnerrel. A GitHub standard nyilvános Actions runner ingyenes; privát repóban a havi kvóta korlátozott. Fizetős csomagot és nagyobb runnert ne kapcsolj be.
2. Settings → Secrets and variables → Actions → Secrets: `TELEGRAM_BOT_TOKEN` és `TELEGRAM_CHAT_ID`. **Soha ne kerüljön token a kódba, commitba, chatbe vagy naplóba.** A chat-azonosító is csak secretként tárolandó; a kód a korábban ellenőrzött címzett SHA-256 ujjlenyomatával ellenőrzi. A címzett a korábban ellenőrzött saját privát chat; a bot csak oda küldhet.
3. Ugyanitt Variables: `ENABLE_SCHEDULE` értéke `true`.
4. Actions → GitHub bounty notifier → Run workflow. A `dry_run` alapból igaz, ilyenkor biztosan nincs küldés. Az éles ellenőrzéshez kapcsold ki. Ellenőrizd, hogy `dry_run: false`, a tesztek és a keresés sikeres. Ha nincs megfelelő új jelölt, nem érkezik Telegram-üzenet.

A cron `2-59/5 * * * *`, azaz az óra 02, 07, 12, … 57. percében indulhat. A GitHub késleltetheti vagy eldobhatja a futást: **nem garantált 5 perces szolgáltatás**. Nyilvános repóban 60 napnyi repóaktivitás-hiány után a GitHub kikapcsolhatja az ütemezést. Kikapcsolás: `ENABLE_SCHEDULE=false`, vagy Disable workflow.

## Ellenőrzés

```sh
python -m unittest discover -s tests -v
python notifier.py --dry-run
```

A dry-run nem küld Telegramot és nem ír értesítési állapotot. A `run-report.json` ideiglenes helyi riport, nincs feltöltve artifactként. A summary csak darabszámokat tartalmaz. A hibák nem naplóznak tokent vagy hitelesített URL-t.

Források: [GitHub schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
