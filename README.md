# GitHub pénzdíjas bounty értesítő

Telegram-értesítő **minden megtalált, nyitott, meghirdetett pénzdíjas GitHub issue-ról**. Az AI-engedély, nehézség, gépigény, hozzárendelés, PR-versenyhelyzet és a kifizetési mód hiánya már nem rejti el a találatot. Ezek ellenőrizendő feltételként jelennek meg. A kiírt összeg nem igazolt finanszírozás vagy garantált kifizetés.

## Keresés

Öt keresés legfrissebb 50–50 találata: bounty címke, bounty a címben, /bounty a törzsben, Expensify HelpWanted és USD a címben. **Ez nem teljes GitHub-index és nem garantál minden történelmi bounty megtalálását.** Minden felfedezett, még nem értesített és a pénzdíj-feltételnek megfelelő issue-t továbbít; nincs 3 találatos küldési korlát. Ötös csomagokban küld, tartós duplikációszűréssel.

- Nincs alsó/felső díjhatár, technológiai, csillagszám-, repókor-, hozzászólásszám- vagy AI-engedélyszűrő.
- USD, EUR, GBP, NIS/ILS, USDC/USDT, BTC és sats összegek felismerése; más pénznemek kimaradhatnak.
- A címben és a leírásban ismételt azonos díj nem teszi érvénytelenné a találatot.
- A friss issue-adatokat újra lekéri. Ha egy gyűjtőoldal eredeti GitHub-issue-t hivatkozik, az eredetit ellenőrzi; lezárt eredetit nem ajánl nyitottként.
- A nullás/testnet/testpénz, díj nélküli bounty-kérés és lezárt issue nem pénzdíjas találat.
- Legutóbbi hozzászólásokból PR- és foglalási figyelmeztetés; ez nem teljes emberi vagy AI-szakmai felülvizsgálat. A meghirdetett összeg értelmezése szabályalapú, tévedhet.
- Nincs automatikus jelentkezés, komment, PR vagy szerződés, és nem hajt végre harmadik féltől származó kódot.

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

A tesztek a tág szűrést, eredeti issue feloldását, ötperces ciklusokat, biztonságos utódindítást, duplikációt és bizonytalan küldést ellenőrzik. A Telegram-személyazonosság és a saját privát címzett ellenőrzése minden éles keresés előtt lefut.

[GitHub ütemezési korlátok](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows) · [GITHUB_TOKEN és workflow_dispatch](https://docs.github.com/en/actions/concepts/security/github_token)
