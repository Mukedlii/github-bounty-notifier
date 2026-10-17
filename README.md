# GitHub pénzdíjas bounty értesítő

Telegram-értesítő a Márk és AI-asszisztense által potenciálisan megoldható, újonnan észlelt pénzdíjas feladatokhoz. Szabályalapú előszűrő, nem önálló AI-mérnök és nem garantálja sem a megoldhatóságot, sem a fizetést.

## Szűrés és dátum

- Csak pozitív, meghirdetett bounty/reward; egy költségkeret vagy ár önmagában nem elég.
- Kódolási, tesztelési, webes, API-, bot-, dokumentum-/adatfeldolgozási, dokumentációs és CI-scope felismerése.
- Gyűjtő- és bevételi radarok, díj nélküli kérések, testnet, nullás díj kizárva.
- Kizárva: felismert AI-tilalom vagy a közreműködő saját emberi technikai ellenőrzésének követelménye, speciális GPU/eszközigény, egyes összetett audit-/konszenzusfeladatok, hozzárendelt issue és 30-nál több komment.
- README, CONTRIBUTING és AGENTS gyökérszabályok, továbbá .github/CONTRIBUTING és .github/AGENTS ellenőrzése. Felismert AI-tilalom esetén nincs értesítés. A szabályok természetes nyelvű értelmezése korlátozott; nincs teljes alkalmassági garancia.
- Legfeljebb 30 hozzászólás és az issue timeline ellenőrzése: claim, kész PR említése vagy kapcsolt PR esetén konzervatívan nem küld. Régi, elutasított PR is okozhat kihagyást.
- AI-használat, díj finanszírozása, Magyarországról való jogosultság és a fizetési mód végleges karbantartói megerősítést igényel; ezt az üzenet jelzi.
- Minden üzenetben **Issue megnyitva**: az eredeti GitHub `created_at`, Europe/Budapest időzónában. Ez nem feltétlenül a pénzdíj későbbi meghirdetésének dátuma; azt nem találja ki, és nem helyettesíti `updated_at`-tal.
- Öt keresés legfrissebb 50–50 találata; nem teljes GitHub-lefedettség. Nincs automatikus jelentkezés vagy PR.
- A már elküldött 41 találat állapota megmarad: régi találat nem kerül újra kiküldésre a szűrő megváltozása miatt.

## Futás és helyreállás

A GitHub cron-indítások a gyakorlatban kimaradtak, ezért önmagukban nem adtak ötperces figyelést. Most egy **15 perces worker háromszor keres, ötperces időközönként**, majd egyszer elindít egy utód-workflow-t. A GitHub cron óránként a 7. percben tartalék indító. A workflow concurrency egyszerre egy aktív workert enged. Az utódindítás a GitHub saját, rövid életű `GITHUB_TOKEN` tokenjét használja, csak ezen a repón, `actions: write` jogosultsággal; nincs új személyes API-kulcs.

A GitHub runner indulása, sorban állása és elérhetősége továbbra is okozhat késést: **nincs garantált, pontos ötperces SLA**. Három egymást követő hibás keresésnél a worker hibával megáll; a tartalék indító később újrapróbálhatja. Bizonytalan Telegram-küldést vagy utódindítást nem ismétel vakon.

A `state/notifications.json` minden éles keresés után tartalmazza a `last_scan_at`, `last_success_at` és a darabszámadatokat. A `pending` értesítés nem jelent igazolt küldést; siker esetén `sent` és a Telegram message_id kerül mellé. Az állapot csak nyilvános issue-adatokat és technikai darabszámokat tartalmaz; tokent nem.

## Bekapcsolás / leállítás

Nyilvános repó, standard `ubuntu-24.04` runner. A folyamatos worker privát repóban megtagadja a futást, így nem kezd privát, kvótás folyamatos futtatásba. Nincs fizetős szolgáltatás vagy AI API.

Actions Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. A címzettet SHA-256 ujjlenyomat ellenőrzi; csak a korábban jóváhagyott saját privát chat használható. A token és a chat-azonosító soha ne kerüljön kódba vagy naplóba.

Repository variable: `ENABLE_SCHEDULE=true`. Kézi indításnál `dry_run=true` az alapbeállítás, ez nem küld és nem ír állapotot. Éles, folytatódó indításhoz `dry_run=false`, `follow_up=true`. Az egyszeri `bootstrap=true` az első keresés után azonnal utódot indít, hogy az átadás tesztelhető legyen; az utód normál ötperces worker.

**Azonnali leállítás:** Disable workflow és Cancel workflow az aktív futáson. A `ENABLE_SCHEDULE=false` az új workerek indulását tiltja, a már futó worker legfeljebb a jelenlegi 15 perces ciklus végéig folytatódhat. Egyetlen próba: `dry_run=false`, `follow_up=false`.

## Tesztek

```sh
python -m unittest discover -s tests -v
python notifier.py --dry-run
```

A tesztek az alkalmassági előszűrést, Budapest szerinti dátumot, eredeti issue feloldását, ötperces ciklusokat, biztonságos utódindítást, duplikációt és bizonytalan küldést ellenőrzik. A Telegram-személyazonosság és a saját privát címzett ellenőrzése minden éles keresés előtt lefut.

[GitHub ütemezési korlátok](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows) · [GITHUB_TOKEN és workflow_dispatch](https://docs.github.com/en/actions/concepts/security/github_token)
