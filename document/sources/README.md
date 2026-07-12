# スクレイピングsite台帳

この文書は、対応済みsiteを既存実装へ案内する台帳です。資格、対象回、URL templateの値は`config/scrape_presets.json`、共通手順は[スクレイピングworkflow](../operations/scraping_workflow.md)を正本とし、ここへ複製しません。

## 先に確認する順序

1. `config/scrape_presets.json`で対象資格と`scraper_type`を探す。
2. 下表から既存entrypoint、認証、test、site固有契約を確認する。
3. 同じDOMと遷移を扱える場合はpresetだけを追加する。
4. parser差分が必要な場合は既存site実装へ追加し、fixture又は最小HTML testを先に作る。
5. 新しいdomainかつ既存parserで表現できない場合だけ、新しい`scraper_type`を追加する。

## 対応site

| `scraper_type` | domain | entrypoint | ページ構成・認証 | 主な検証 |
| --- | --- | --- | --- | --- |
| `kakomonn` | `*.kakomonn.com` | `code.py` | 一覧をページ送りし、各問題ページと解答endpointを取得。公開ページ。 | `tests/test_kakomonn_inventory.py`, `tests/test_scrape_identity_keys.py`, `tests/test_scrape_presets.py` |
| `gassyunin` | `gassyunin.com` | `scrape_gassyunin.py` | 年度ごとの単一ページ内に5科目。公開ページ。 | [抽出契約](gas-shunin/gassyunin_source_contract.md), `tests/test_scrape_gassyunin.py` |
| `sgsiken` | `sg-siken.com`, `nw-siken.com` | `scrape_sgsiken.py` | 一覧から午前問題と午後の共通問題ページを収集。公開ページ。 | `tests/test_scrape_sgsiken.py`, `tests/test_scrape_presets.py` |
| `kurohon` | `kurohon.jp` | `scrape_kurohon.py` | 1試験回のページから問題ブロックと正答表を対応付ける。公開ページ。 | `tests/test_scrape_kurohon.py`, `tests/test_scrape_presets.py` |
| `mecnet` | `study.mecnet.jp` | `scrape_mecnet_kokushi.py` | 一覧、ページ送り、解説ページ。ログイン必須。 | `tests/test_scrape_presets.py`, `tests/test_mecnet_kokushi_category_build.py` |
| `kougai` | `yaku-tik.com`, `qualification-text.com`, `zoron.hatenablog.com` | `scrape_kougai.py` | domainごとに一覧発見と問題parserを切り替えるmulti-source adapter。公開ページ。 | `tests/test_scrape_kougai.py`, `tests/test_scrape_identity_keys.py` |

## site別の要点

### kakomonn.com

- 標準URLは`/list1/<group>?page=1`。ページ送りと保存済みgroupの再開処理は既存実装を使う。
- source固有IDは問題URL由来、canonical identityは資格、試験回、問番号から作り、別siteの同一問題と衝突させない。
- 未対応資格の棚卸しと一括再開は`scripts/scrape/kakomonn_inventory.py`を使う。個別資格用の新しいscraperを作らない。
- 問題ページ又は解答endpointのtimeoutはgroup単位で再実行し、既存`00_source`を上書きしない。

### gassyunin.com

- URLは`/exam/<grade>/<grade>_<year>/`。`grade`は`kou`又は`otsu`。
- 科目tab、`各選択肢の判定`、数値選択肢の2形式を扱う。詳細は[抽出契約](gas-shunin/gassyunin_source_contract.md)を正本とする。
- 1年度の期待構成は法令16、基礎理論15、製造9、供給9、消費機器9の計58問。件数差があればDOM変更として停止・再監査する。

### sg-siken.com / nw-siken.com

- source側の回表記と出力`list_group_id`が異なるため、`scrape_targets`で明示的に対応付ける。
- 午前問題と午後問題ではDOMとID粒度が異なる。午後は共通問題文、設問、空欄を既存parserで分解する。
- 新しい系列domainでも同じページ構造なら`scraper_type=sgsiken`を再利用し、live testで一覧URLと1問を確認する。

### kurohon.jp

- URL中の回数と`examYear`は同じ値とは限らないため、`scrape_targets`のsource/output対応を正本とする。
- ページ上部の正答表と問題の出現順を対応付ける。正答数と問題数が合わない回は保存せず調査する。
- 柔道整復師、鍼灸師、あん摩マッサージ指圧師は同じparserを共有する。

### study.mecnet.jp

- `~/.config/exam_scraper/secure.env`の`MECNET_COOKIES_JSON`、又は`MECNET_USERID`と`MECNET_PASSWORD`で認証する。秘密情報をrepoへ保存しない。
- 既定のアクセス間隔は`MECNET_MIN_DELAY_SEC`と`MECNET_MAX_DELAY_SEC`で制御する。認証失敗と問題0件を同じ扱いにしない。
- 出題回の発見、一覧、解説ページ取得は既存実装に集約し、補助scriptを新しい日常入口にしない。

### 公害防止管理者multi-source

- `scrape_kougai.py`がdomainを判定し、yaku-tik、qualification-text、zoronのURL発見とparserを切り替える。
- 同一年度を複数sourceから取得できるため、site provenanceとcanonical identityを分離する。
- source別filename suffixはrunnerが付与する。既存sourceがある年度へ別sourceを追加するときも上書きしない。

## 更新ルール

- 新しい`scraper_type`又はdomainを追加したら、この台帳、runner mapping、preset、parser testを同じ変更で更新する。
- HTML selectorや正答解釈の詳細はsite固有契約へ置き、この台帳には入口と例外だけを書く。
- 日付付きの取得結果や単発監査は`document/temporary/audits/`へ置く。
