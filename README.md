tigraoRADIO

Bot de Telegram integrado ao Spotify para mostrar a música atual ou a última música ouvida, registrar reproduções, curtidas, ranking e análise musical.

Funcionalidades reais

* Mostrar a música atual ou a última música ouvida via Spotify
* Registrar reproduções por faixa
* Curtir e descurtir músicas pelos botões inline
* Exibir perfil musical do usuário
* Exibir ranking do grupo
* Analisar o mood musical da faixa atual com dados de áudio
* Criar links privados de entrada no grupo para o owner
* Aprovar ou banir pedidos de entrada via comandos privados

Comandos

* /start
* /help
* /login
* /playing
* /mood
* /myself
* /songcharts
* /logout

Gatilhos textuais

Os textos abaixo podem acionar a mesma lógica de /playing:

* tocando
* kur
* xxt
* ts
* cebrutius
* tigraofm
* djpi
* royalfm
* geeksfm
* radinho
* qap

Como funciona

/playing

Busca a música atual no Spotify. Se não houver música em execução, tenta a última música ouvida. Registra a reprodução, calcula plays e likes da faixa, mostra capa do álbum e adiciona botões inline para plays e likes.

/mood

Usa a faixa atual/última faixa para consultar dados de áudio (valence, energy, danceability). Quando há histórico suficiente do usuário com dados enriquecidos, calcula médias e tendência. Quando não há, usa fallback e dispara enriquecimento em background.

/myself

Mostra estatísticas pessoais:

* top músicas
* top artistas
* total de curtidas

/songcharts

Mostra estatísticas do grupo:

* top músicas
* top artistas
* músicas mais curtidas

Banco de dados

Tabelas confirmadas:

* spotify_tokens
* track_plays
* track_likes
* track_audio_features
* join_requests

Deploy

O projeto está configurado para Railway.

* start command: python -m app.bootstrap
* healthcheck: /healthz

Variáveis de ambiente

* TELEGRAM_BOT_TOKEN
* SPOTIFY_CLIENT_ID
* SPOTIFY_CLIENT_SECRET
* BASE_URL
* DATABASE_URL (opcional; se ausente, usa SQLite em /data/app.db)
