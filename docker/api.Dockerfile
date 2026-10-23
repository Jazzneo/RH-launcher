FROM docker.io/library/node:22-bookworm-slim
WORKDIR /app
COPY sources/RustyHearts-API-master/package.json ./
RUN npm install --omit=dev && npm cache clean --force
COPY sources/RustyHearts-API-master/config ./config
COPY sources/RustyHearts-API-master/src ./src
COPY sources/RustyHearts-API-master/public ./public
CMD ["node", "src/app.js", "mainApp", "usaApp", "jpnApp", "proxyServer"]
