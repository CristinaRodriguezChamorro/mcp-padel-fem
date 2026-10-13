# 🎾 MCP Pádel Femenino

Servidor **MCP** con datos reales del pádel profesional femenino (circuito Premier Padel / FIP). Cualquier asistente de IA que hable MCP (Copilot en VS Code, Claude, etc.) puede llamar a sus herramientas: el servidor devuelve los datos ya filtrados, con su fuente y su antigüedad, y el propio asistente redacta la respuesta. Incluye además una **web de apoyo** con el ranking, los torneos y las noticias.

> Proyecto no oficial: sin relación con la FIP, con Premier Padel, con Padel API ni con los medios citados.

## Qué es

| Parte | Qué hace | Dónde está |
|---|---|---|
| **El MCP** | Tres herramientas (ranking, último torneo y próximo torneo). Devuelve datos, no redacta nada | `/ai/mcp` · carpeta `apps/mcp-server/padel_mcp/` |
| **La web** | Ranking, torneos, noticias y una página que explica la arquitectura «para todos» (`/como-funciona`) | `/` · `server.py` y `static/` |
| **Los datos** | Una tarea diaria los pide a su fuente, los valida y los guarda en `data/`. La web y el MCP solo leen esos archivos | carpeta `tools/` |

```text
Tú preguntas → el agente (Copilot, Claude…) elige una herramienta → el MCP devuelve datos filtrados → el agente redacta la respuesta
```

El servidor decide **qué datos** devuelve; el agente decide **cómo contarlos**. El servidor no puede imponer una tabla o un tono: eso se pide en las instrucciones de tu cliente (ver más abajo).

## Empezar en local

Necesitas Python 3.12 (vale 3.11).

```bash
cd apps/mcp-server
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
export PADELAPI_TOKEN="tu-token"   # PowerShell: $env:PADELAPI_TOKEN="tu-token"
python server.py
```

- La web está en <http://localhost:8000> y el MCP en <http://localhost:8000/ai/mcp>.
- El token de [Padel API](https://padelapi.org) es gratuito y **opcional**: sin él se usa Wikipedia, con más retraso (ver más abajo). Va **solo** como variable de entorno, nunca en un archivo ni en el repositorio.
- Con token, la primera actualización tarda unos 3-4 minutos (hace unas veinte peticiones con pausa entre ellas). Abre `/api/data-status`: `ingesta.ok` debe ser `true` y `fuente_activa` debe ser `padelapi`. Hasta entonces se muestran los datos de ejemplo de `data/`, marcados como tales.
- La actualización reescribe `data/ranking.json` y `data/calendario.json`; no los subas a git (`git checkout -- apps/mcp-server/data` los restaura).
- Con Docker: `docker compose up --build` (usa `docker-compose.yml` y pasa las variables de tu entorno).

## Conectarlo a VS Code (Copilot)

1. Con el servidor en marcha, crea `.vscode/mcp.json` (o copia `examples/vscode-mcp-local.json`):

   ```json
   {
     "servers": {
       "padel-femenino": { "type": "http", "url": "http://localhost:8000/ai/mcp" }
     }
   }
   ```

2. Pulsa **Start** sobre el servidor en ese archivo.
3. Abre Copilot Chat en modo **Agent** y comprueba, en el icono de herramientas, que salen las tres.
4. Pregunta: *«Dame el top 10 del ranking femenino con sus nombres y sus puntos»*, *«¿Quién ganó el último torneo y por cuánto?»* o *«¿Cuándo y dónde es el próximo torneo?»*. Despliega la llamada en el chat para ver lo que devolvió el servidor.

Para un servidor desplegado con acceso por GitHub, usa `examples/vscode-mcp.json` (VS Code pide el token al arrancar, con la entrada oculta).

**Tu estilo, en tus instrucciones.** Por ejemplo, en `.github/copilot-instructions.md`:

```text
Cuando pregunte por pádel femenino, usa el MCP.
Responde en español, breve y con una tabla si hay varias jugadoras.
Si los datos tienen retraso, dímelo en una línea.
No inventes datos que el MCP no devuelva.
```

## Las herramientas del MCP

Solo lectura. Por defecto el servidor expone **tres**:

| Herramienta | Qué devuelve |
|---|---|
| `ranking_femenino` | Posición, nombre, país y puntos de cada jugadora, de la 1.ª a la `top` (por defecto el top 10, máximo 20) |
| `ultimo_torneo` | El último torneo terminado: dónde fue, qué tipo de torneo es, quién jugó la final, primeras, segundas y terceras, resultado final y una frase que lo resume |
| `proximo_torneo` | El próximo torneo: nombre, ciudad y país, fechas, días que faltan, tipo de torneo y dónde verlo; si hay uno en juego, también ese |

Otras herramientas, apagadas por defecto: se activan con `MCP_TOOLS=extended`.

| Herramienta | Para qué |
|---|---|
| `jugadora` | Ficha de una jugadora: ranking, finales jugadas y noticias que la mencionan |
| `comparar_jugadoras` | Dos jugadoras lado a lado |
| `torneo_actual` | Torneo en juego y enlace al marcador oficial |
| `resultado_torneo` | Ficha de la final femenina de cualquier torneo ya jugado (por nombre o por jugadora) |
| `resultados_recientes` | Finales femeninas ya jugadas, con filtro opcional por jugadora |
| `calendario` | Próximos torneos |
| `noticias` | Noticias con enlace a la fuente (según la fuente: resumen, extracto literal o solo titular), con filtro opcional por jugadora |
| `resumen_del_dia` | Panorama rápido |
| `estado_de_los_datos` | Fuente, antigüedad y última actualización de cada dato |

Cada respuesta indica su fuente, su antigüedad y los avisos (datos de ejemplo, datos con retraso). Cuando falta un dato lo dice en una nota en vez de inventarlo: por ejemplo, las terceras (las semifinalistas, porque en pádel no se juega partido por el tercer puesto) solo se dan si la fuente aporta las semifinales. **No hay marcadores en directo**: se enlaza al marcador oficial.

## De dónde salen los datos

| Dato | Fuente |
|---|---|
| Ranking, calendario y resultados | **Padel API** (padelapi.org) si hay `PADELAPI_TOKEN`; si no hay token, o falla y todavía no hay datos buenos de la API guardados, la API de **Wikipedia** (página *Premier Padel <año>*, CC BY-SA 4.0) |
| Noticias | Los **feeds RSS** de los medios, con una regla por fuente (ver abajo) |
| Marcadores en directo | No se reproducen: enlace al marcador oficial |
| «Qué usan» y «¿Sabías que?» | Textos escritos a mano en `static/index.html`, sin fuente automática |

**Padel API.** Servicio comercial con datos de Premier Padel (Major, P1, P2 y Finals) y un plan gratuito: ranking mundial actual, calendario y resultados de los últimos 6 meses, 50.000 peticiones al mes (10 por minuto y 2.000 al día), con cuadros completos desde 2023, de donde salen las semifinalistas. La tarea diaria hace unas pocas peticiones con una pausa de 6,5 s entre ellas y no vuelve a pedir un torneo ya completado. Sus [condiciones](https://padelapi.org/terms-of-service) permiten usar los datos en tu propia aplicación y guardar las respuestas; **no** permiten revenderlos como producto, copiar su base de datos ni extraerla en masa. Por eso el MCP ofrece respuestas filtradas y limitadas, y no un paso directo de su API. Sus nombres de jugadoras vienen sin tildes.

**Wikipedia (respaldo).** La mantienen voluntarios, así que el ranking y los resultados pueden ir con **semanas de retraso**. Solo recoge ganadoras y finalistas, sin semifinales. La web y el MCP avisan cuando los datos pasan de 14 días.

**Noticias.** Se consultan como mucho cada 10 minutos, solo las que hablan de alguna jugadora del Top 20 y se publican como máximo 15. **Nunca de hombres**: manda el titular. Si el titular nombra a un jugador, o dice «masculino», la noticia se descarta; un titular solo con apellidos no basta (muchos son también de hombres) y hace falta que diga expresamente que es femenino. Para saber quiénes son ellos, la tarea de actualización guarda también el ranking masculino de Padel API (`data/ranking_masculino.json`, solo nombres: no se muestra en ningún sitio). Sin esa lista (por ejemplo con el respaldo de Wikipedia, o unos minutos tras un despliegue) el filtro es más estricto: solo pasan las noticias que digan expresamente femenino, mujeres, campeonas o jugadoras. Cada medio se trata según lo que permiten sus condiciones; cualquier fuente nueva empieza en «solo titular y enlace» hasta que se lean las suyas:

| Nivel | Qué se publica | Medios |
|---|---|---|
| resumen | Título, resumen propio (con IA si hay `GROQ_API_KEY`), autor y enlace | OKDiario (licencia Creative Commons no comercial) |
| extracto | Título, extracto corto literal y enlace | Padel Addict |
| solo titular y enlace | Título, fecha, medio y enlace | Marca, Mundo Deportivo, Padel Spain, Set Padel Magazine |

**Por qué no la web de la FIP.** Sus condiciones de uso prohíben los robots y la extracción de datos, y copiar su contenido sin licencia escrita. Esta aplicación **no lee la web de la FIP**: solo enlaza a su marcador oficial.

## Quién puede conectarse (`MCP_AUTH`)

| Modo | Cómo se activa | Qué exige |
|---|---|---|
| **GitHub** | `MCP_AUTH=github` | Un token de GitHub en `Authorization: Bearer <token>` |
| **Token fijo** | `MCP_TOKEN` (y, si quieres, `MCP_AUTH=token`) | Ese token |
| **Abierto** | `MCP_AUTH=open`, o no definir nada | Nada, solo un límite de peticiones |

Si `MCP_AUTH` tiene un valor desconocido o contradictorio (por ejemplo `open` junto con `MCP_TOKEN`), el servidor **se niega a arrancar** en lugar de abrirse por error. Si no defines nada, el endpoint queda **abierto** y lo avisa al arrancar: sirve para probar, no para dejarlo público.

**Modo GitHub.** El servidor pregunta a GitHub de quién es el token (`GET https://api.github.com/user`) y deja pasar a esa cuenta:

- `MCP_GITHUB_USERS=cris,otra` limita el acceso a esas cuentas; vacío = cualquier cuenta de GitHub válida.
- Cada cuenta tiene su propio límite (`MCP_USER_RATE_PER_MIN`), además del límite por IP.
- El resultado se guarda unos minutos en memoria (solo una huella SHA-256, nunca el token) y las comprobaciones contra GitHub están limitadas.
- El token **no necesita ningún permiso**: crea uno solo de identidad y con caducidad corta.
- No sirve para los conectores de claude.ai, que no envían una cabecera fija.

## Desplegar en Railway

1. Sube el proyecto a un repositorio de GitHub (puede ser privado).
2. En Railway: **New Project → Deploy from GitHub repo** y elige ese repositorio. `railway.toml` indica el `Dockerfile` (`apps/mcp-server/Dockerfile`).
3. En **Variables** añade (los valores nunca van en el repositorio):
   - `PADELAPI_TOKEN`: tu token de Padel API.
   - `MCP_AUTH`: `open` para la primera prueba; después `github` (y, si quieres, `MCP_GITHUB_USERS`).
   - `SCRAPER_CONTACT`: tu correo, para identificar al programa ante los medios.
4. En **Settings → Networking → Generate Domain** obtén la dirección pública.
5. Espera unos 4 minutos tras el arranque y abre `https://TU-APP.up.railway.app/api/data-status`: `ingesta.ok` debe ser `true` y `fuente_activa` `padelapi`. El disco de Railway es efímero: tras cada despliegue los datos se vuelven a pedir.
6. El MCP queda en `https://TU-APP.up.railway.app/ai/mcp`.

Si el despliegue falla, el log de Railway dice dónde. Si se queja de no encontrar `requirements.txt` al construir, el contexto de construcción no es `apps/mcp-server`: revisa el *Root Directory* del servicio.

## Variables de entorno

| Variable | Para qué | Por defecto |
|---|---|---|
| `PORT` | Puerto del servidor | `8000` |
| `DATA_DIR` | Carpeta de los archivos de datos | `apps/mcp-server/data` |
| `PADELAPI_TOKEN` | Token de Padel API: activa la fuente principal (¡solo como variable del servidor!) | — |
| `PADELAPI_INGEST` | `off` desactiva Padel API aunque haya token | activa |
| `PADELAPI_MIN_INTERVAL` | Segundos entre peticiones a Padel API | `6.5` |
| `WIKIPEDIA_INGEST` | `off` desactiva toda la actualización de datos (Padel API y Wikipedia) | activa |
| `WIKIPEDIA_INGEST_HOURS` | Horas entre actualizaciones correctas (mínimo 1) | `24` |
| `INGEST_START_DELAY` | Segundos de espera tras arrancar antes de la primera actualización | `15` |
| `MCP_TOOLS` | `extended` añade las herramientas extra | solo las tres |
| `MCP_AUTH` | Modo de acceso al MCP: `github`, `token` u `open` | automático |
| `MCP_GITHUB_USERS` | Cuentas de GitHub permitidas (separadas por comas) | cualquiera |
| `MCP_TOKEN` | Token fijo de acceso al MCP | — |
| `MCP_RATE_PER_MIN` | Peticiones por minuto al MCP, por IP | `120` |
| `MCP_USER_RATE_PER_MIN` | Peticiones por minuto al MCP, por cuenta de GitHub | `120` |
| `MCP_GLOBAL_RATE_PER_MIN` | Peticiones por minuto al MCP, en total | `600` |
| `NEWS_RSS_ENABLED` | `false` apaga las noticias | activas |
| `NEWS_RSS_FEEDS` | Sustituye la lista de feeds (URLs separadas por comas); los que no estén en la tabla de niveles se tratan como «solo titular y enlace» | los de la tabla |
| `GROQ_API_KEY` | Resúmenes de noticias con IA (sin ella, resumen determinista) | — |
| `GROQ_NEWS_MODEL` | Modelo de Groq para los resúmenes | `llama-3.1-8b-instant` |
| `SCRAPER_CONTACT` | Tu correo, para identificar al programa ante los medios y Wikipedia | — |
| `SCRAPER_INFO_URL` | URL con información del programa (para el User-Agent) | — |
| `SCRAPER_USER_AGENT` | User-Agent completo, si quieres sustituir el automático | automático |
| `SCRAPER_MIN_INTERVAL` | Segundos mínimos entre peticiones al mismo dominio | `1.0` |

## Estructura del proyecto

```text
.
├── README.md
├── railway.toml           # indica a Railway qué Dockerfile usar
├── docker-compose.yml     # para ejecutarlo con Docker en local
├── .gitignore
├── examples/
│   ├── vscode-mcp-local.json   # servidor local, sin acceso
│   └── vscode-mcp.json         # servidor desplegado, con token de GitHub
└── apps/mcp-server/
    ├── server.py          # LA WEB (/api/*, páginas) y la tarea de actualización; solo monta el MCP
    ├── Dockerfile
    ├── requirements.txt
    ├── padel_mcp/         # EL MCP (nada de web): lo que habla con cualquier modelo
    │   ├── tools.py       #   las herramientas y la puerta de acceso (límites, token, GitHub)
    │   └── auth.py        #   verificación de tokens de GitHub
    ├── tools/             # capa de datos COMPARTIDA por la web y el MCP (no son herramientas del MCP)
    │   ├── padelapi_source.py   # fuente principal: Padel API (con token, a ritmo y sin repetir peticiones)
    │   ├── wikipedia_source.py  # respaldo: lee la página de la temporada (API de MediaWiki)
    │   ├── ingest.py            # elige la fuente, valida y guarda; actualización diaria
    │   ├── local_data.py        # lee data/*.json (sin red) y arma las fichas de torneo
    │   ├── news.py              # noticias por RSS, con regla por fuente y filtro Top 20
    │   ├── players.py           # reconoce jugadoras del Top 20 en un texto
    │   └── http_policy.py       # User-Agent honesto, robots.txt y ritmo por dominio
    ├── data/              # datos de arranque (de ejemplo); la tarea de actualización los reescribe y añade ranking_masculino.json
    │   ├── ranking.json
    │   ├── calendario.json
    │   ├── torneo.json          # torneo en juego puesto a mano (opcional): con "active": true tiene prioridad
    │   └── torneo.ejemplo.json  # plantilla de torneo.json
    ├── static/
    │   ├── index.html
    │   └── como-funciona.html   # la arquitectura explicada para todos
    └── tests/             # pruebas sin internet
```

## Endpoints de la web

El MCP está en `/ai/mcp`. La web expone:

```text
GET /                          la web
GET /api/ranking               ranking femenino
GET /api/calendar              torneos actuales y próximos
GET /api/live                  torneo en curso (sin marcadores) y enlace al marcador oficial
GET /api/news                  noticias
GET /api/data-status           fuente, antigüedad y resultado de la última actualización
GET /api/version               versión
GET /como-funciona             la arquitectura explicada para todos
```

FastAPI añade además `/docs`, `/redoc` y `/openapi.json` (ver las limitaciones).

## Pruebas

```bash
cd apps/mcp-server
python -m unittest discover -s tests -v
```

No necesitan internet ni tokens. Incluyen una barrera que falla si el código apunta a un dominio que no esté en la lista permitida o si se reintroduce una librería de scraping, y un test que compara este README con el código (herramientas, variables de entorno, rutas, archivos y árbol de carpetas).

## Uso responsable y licencias

- El programa se identifica como `PadelFemBot/1.0`, respeta `robots.txt` en los feeds y espera entre peticiones al mismo dominio. A Wikipedia le pide una página al día.
- Wikipedia: CC BY-SA 4.0; la web cita la fuente al pie. Padel API: sus condiciones, arriba. OKDiario exige **atribución con enlace directo, sin `nofollow`**, a cada artículo; la web ya lo hace. Conviene releer las condiciones de vez en cuando.
- «Qué usan» no contiene datos de contratos y avisa de que el material no está verificado con las marcas. Si algún día cobras de una marca o añades enlaces de afiliación, pasa a ser publicidad y las fuentes con licencia no comercial (OKDiario) dejarían de valer.
- El historial de git de un repositorio que haya contenido versiones anteriores puede conservar código antiguo de scraping. Si vas a hacerlo público y eso te preocupa, empieza un repositorio nuevo.

Si eres titular de una web usada como fuente y prefieres que no se acceda a ella, escribe al contacto del repositorio y se retirará.

## Limitaciones conocidas

- **Retraso y alcance.** Con Padel API gratuita solo hay resultados de los últimos 6 meses. Con el respaldo de Wikipedia, semanas de retraso y sin terceras. Solo se publican las finales femeninas, no las rondas previas ni marcadores en directo.
- **Arranque.** El disco de Railway es efímero: tras cada despliegue se vuelven a pedir los datos y durante unos 3-4 minutos se muestran los de ejemplo.
- **Noticias.** Hay pocas (los medios publican unas pocas de pádel a la semana), como máximo 15, y la mayoría solo con titular y enlace.
- **Superficie pública.** FastAPI publica `/docs`, `/redoc` y `/openapi.json`. El MCP en modo abierto no identifica a nadie: para uso público, `MCP_AUTH=github`.
- **Padel API.** Si el MCP se hace muy público, conviene confirmar con ellos que su uso encaja con sus condiciones.

## Objetivo

Caso práctico para combinar **APIs, fuentes abiertas, procesamiento asíncrono, IA y Model Context Protocol (MCP)** en una aplicación real.
