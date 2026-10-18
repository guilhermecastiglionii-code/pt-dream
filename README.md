# PT Dream

Painel da PT no Tibia (mundo Collabra): level, XP diária, metas, ranking do servidor, boost/TC e vendas.

- **Site:** GitHub Pages (pasta raiz da branch `main`).
- **Atualização:** `.github/workflows/update.yml` roda todo dia às 07:12 e 13:12 (Brasília), lê o highscore na API TibiaData e grava em `data/`.
- **Edição (vendas, chars, metas, servidores):** Firebase Firestore. Só e-mails liberados em `firestore.rules` (ou no campo `editores` de `config/main`) podem editar.

Para rodar a atualização na hora: aba **Actions** → **Atualizar dados** → **Run workflow**.
