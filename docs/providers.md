# Провайдеры данных

Проверено живыми запросами и по официальной документации (октябрь 2026). Лимиты у провайдеров меняются, поэтому адаптеры не полагаются на точные цифры: соблюдают паузу между запросами, повторяют при 429/5xx с учётом `Retry-After`.

| Сеть | Источник | Ключ | Лимит без ключа | Что берём |
| --- | --- | --- | --- | --- |
| Bitcoin | mempool.space `GET /api/address/{a}` | не нужен | не опубликован; при превышении 429, при злоупотреблении бан | `chain_stats` + `mempool_stats`: funded − spent, сатоши |
| Dogecoin | dogechain.com `GET /api/v1/address/balance/{a}`, запасной — BlockCypher `GET /v1/doge/main/addrs/{a}/balance` | не нужен | dogechain — не опубликован; BlockCypher — 3 в секунду и 100 в час | баланс в DOGE; у BlockCypher `final_balance` в koinu (10⁻⁸) |
| Litecoin | litecoinspace.org `GET /api/address/{a}` (тот же API, что у mempool) | не нужен | не опубликован | то же, литоши |
| TON | TonAPI `GET /v2/accounts/{a}`, `/v2/accounts/{a}/jettons` | желательно, `Authorization: Bearer` | 1 RPS (бесплатный уровень тоже 1 RPS) | баланс в нанотонах; жетоны с decimals, symbol и `verification` (whitelist / none / blacklist) |
| Tron | TronGrid `GET /v1/accounts/{a}`, `POST /wallet/triggerconstantcontract` | желательно, `TRON-PRO-API-KEY` | динамический, при превышении 403/429 и блок до 30 с | `balance`, застейканный TRX (Stake 2.0), сырые TRC-20 без decimals |
| Solana | публичный RPC `api.mainnet-beta.solana.com` или Helius RPC | Helius — да | публичный: заголовки показывают 40 соединений, лимиты по методам; тяжёлые методы быстро дают 429 | `getBalance`, `getTokenAccountsByOwner` для Token и Token-2022 (с decimals) |
| Цены | CoinGecko `api.coingecko.com/api/v3` | Demo-ключ по желанию, `x-cg-demo-api-key` | общий лимит по IP, без цифр | `/simple/price` одним запросом на всё; `/coins/list?include_platform=true` раз в сутки |

## Что выяснилось

- **Tronscan API теперь требует ключ** (без него 401). Поэтому TRC-20 берём из TronGrid: он отдаёт только контракт и сырое количество. Decimals читаем вызовом `decimals()` и кэшируем навсегда, а символ и название берём из CoinGecko.
- **Спам.** На реальных адресах большая часть TRC-20 и SPL-токенов — мусорные раздачи, а у активных Solana-кошельков бывают тысячи токен-аккаунтов. Поэтому на Solana, Tron и TON показываются только токены, которые знает CoinGecko (на TON ещё и жетоны с `verification: whitelist`). Остальные считаются как «скрыто N неизвестных токенов» и в итог не входят.
- **CoinGecko без ключа работает.** Demo-ключ: 100 запросов в минуту; по сторонним источникам также до 10 000 в месяц. На одно обновление уходит один запрос цен, плюс список монет раз в сутки (~4 МБ). При обновлении раз в 5 минут, пока приложение открыто, лимит не выбирается.
- **CoinGecko хранит TON-контракты в friendly-форме** (`EQ…`), а TonAPI отдаёт raw (`0:hex`). Сопоставляем через ту же нормализацию, что и при проверке адресов.
- **Solana.** На mainnet уже встречаются транзакции версии 1. Для балансов это не важно, но `getTransaction` без `maxSupportedTransactionVersion: 1` падает.
- **Helius Free:** 1 млн кредитов в месяц, 10 RPC-запросов в секунду, 2 запроса в секунду к DAS. Helius используется как обычный RPC с более мягкими лимитами, чем у публичного.
- **Стейкинг.** Застейканный TRX приходит в том же ответе TronGrid; TON в номинатор-пулах — из TonAPI; нативный стейкинг Solana — через стейк-аккаунты (`getProgramAccounts`). Ликвидный стейкинг (jitoSOL, tsTON и т. п.) — обычные токены.

## Источники

- [mempool.space REST API](https://mempool.space/docs/api/rest)
- [CoinGecko: ошибки и лимиты](https://docs.coingecko.com/docs/common-errors-rate-limit), [тарифы](https://www.coingecko.com/en/api/pricing)
- [TonAPI: бесплатные лимиты](https://docs.tonconsole.com/tonapi/dapp/free-limits)
- [Helius: тарифы](https://www.helius.dev/docs/billing/plans)
- [TronGrid: лимиты](https://developers.tron.network/reference/rate-limits)
