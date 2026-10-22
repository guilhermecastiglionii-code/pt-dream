# PT Dream

Painel da PT no Tibia (mundo Collabra): level, XP diária, metas, ranking do servidor, boost/TC e vendas.

- **Site:** GitHub Pages (pasta raiz da branch `main`).
- **Atualização:** `.github/workflows/update.yml` roda 5 vezes por dia depois do server save (10h de Berlim), lê o highscore na API TibiaData e grava em `data/`. Só aceita páginas com dados de depois do server save; a primeira leitura válida do dia fica e as seguintes só completam o que falta. O ranking do GuildStats só é gravado quando a XP dele bate com a diferença do highscore oficial.
- **Edição (vendas, chars, metas, servidores):** Firebase Firestore. Só e-mails liberados em `firestore.rules` (ou no campo `editores` de `config/main`) podem editar.

Para rodar a atualização na hora: aba **Actions** → **Atualizar dados** → **Run workflow**.
