# media-api メモ

- 現状の media-api はコンテナ内で Remotion CLI（`npm run render:project`）を直接実行しているため、Remotion 側の更新や headless Chrome の変更があるたびに media-api イメージも再ビルドが必要になる。
- `/tmp` をホストにマウントしているので、レンダリングのたびに生成される `node-jiti` や `puppeteer_dev_chrome_profile-*` などの一時ファイルが永続化される。キャッシュとしては有用だが、不要な場合は手動で削除する必要がある。
- Remotion 変更への追従を簡略化するには、既存の `remotion` サービスをレンダリング専用コンテナとして扱い、media-api からは `docker exec remotion ...` などでレンダリングを依頼する構成（もしくは serverless renderer）にするのが望ましい。
- CLI 出力ではフォントや public assets の解決が Studio と異なるため、文字化けや音声が鳴らない問題が起きやすい。Remotion の実行環境を専用コンテナ側に集約し、media-api は結果ファイルのみを受け取る形にして依存を分離する必要がある。
