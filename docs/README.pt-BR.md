# WorkBuddy Daily

**Suite de automação em arquivo único para as atividades diárias do WorkBuddy CN e Internacional.**

[简体中文](../README.md) | [English](README.en.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Português (BR)](README.pt-BR.md)

> Este é um guia rápido em português. A [documentação completa em chinês simplificado](../README.md) continua sendo a referência técnica principal.

## Visão geral

| Item | Detalhes |
| :--- | :--- |
| Objetivo | Check-in diário, tarefas de crescimento, tarefas do mini-programa e coleta verificada de recompensas |
| Credenciais | CN: `WORKBUDDY_REFRESH_TOKEN`; Internacional: `WORKBUDDY_INTL_TOKENS` |
| Ambiente | Python 3.8+, Qinglong, GitHub Actions, Windows ou Linux |
| Dependência principal | `requests` |
| Verificação | Relê o estado do check-in, o progresso das tarefas e o resultado das recompensas |

## Ferramentas incluídas

- `workbuddy_daily.py`: tarefas diárias da versão CN, mini-programa, jogos e relatório.
- `workbuddy_login.py`: login por SMS que gera `phone:AT:RT`.
- `workbuddy_intl_daily.py`: atividade diária e keepalive da versão internacional.
- `workbuddy_intl_login.py`: login por autorização de dispositivo.
- `credits_report.py`: relatório opcional de saldo e validade dos créditos.

## Início rápido

```bash
pip install requests
python workbuddy_login.py
```

Configure o valor retornado:

```text
WORKBUDDY_REFRESH_TOKEN=phone:AT:RT
```

No Qinglong, execute pelo runner de tarefas do painel:

```bash
task workbuddy_daily.py
```

Agendas recomendadas:

```cron
0 7,12 * * *
30 23 * * *
```

A segunda agenda é necessária para a atividade noturna.

## GitHub Actions

Faça Fork ou Import do repositório e adicione os Secrets em **Settings > Secrets and variables > Actions**. Comece com `WORKBUDDY_REFRESH_TOKEN`; para a versão internacional, configure também `WORKBUDDY_INTL_TOKENS`. Execute o workflow manualmente antes de ativar a agenda.

## Versão internacional

```bash
python workbuddy_intl_login.py
python workbuddy_intl_daily.py
```

As sessões das versões CN e internacional não são intercambiáveis.

## Variáveis principais

| Variável | Finalidade |
| :--- | :--- |
| `WORKBUDDY_REFRESH_TOKEN` | Contas CN, uma linha `phone:AT:RT` por conta |
| `WORKBUDDY_INTL_TOKENS` | Credenciais das contas internacionais |
| `WORKBUDDY_TASKS` | Lista opcional de tarefas permitidas |
| `WORKBUDDY_SKIP_TASKS` | Lista opcional de tarefas ignoradas |
| `WORKBUDDY_STATE_DIR` | Diretório persistente para tokens rotacionados |
| `PUSHPLUS_TOKEN`, `BARK_URL`, `WXPUSHER_APP_TOKEN`, `WECOM_WEBHOOK`, `DINGTALK_WEBHOOK` | Canais opcionais de notificação |

## Segurança

Nunca envie AT, RT, resultados de login ou arquivos de estado para o Git. Use variáveis de ambiente ou GitHub Secrets criptografados e mantenha os arquivos de estado fora do repositório.

## Limites de uso e redistribuição

O repositório oficial, scripts, documentação e Releases são gratuitos. Não os repackage como recursos pagos, remova a atribuição, personifique o autor, use a identidade ou os QR codes do autor como endosso, nem reposte o projeto em contas ou grupos não relacionados para desviar tráfego. Consulte o [Aviso de uso e redistribuição](../NOTICE.md). Violações podem resultar em pedidos de remoção; o autor pode pausar atualizações, arquivar ou excluir o repositório.

## Links

- [Releases](https://github.com/L0NE-6/WorkBuddy-Daily/releases)
- [Issues](https://github.com/L0NE-6/WorkBuddy-Daily/issues)
- [Changelog](../CHANGELOG.md)
- [Documentação completa em chinês](../README.md)
