# 🎾 MCP de Pádel Femenino

Servidor MCP con datos reales del pádel profesional femenino. Cualquier asistente de IA que hable MCP (Copilot en VS Code, Claude, etc.) puede llamar a sus herramientas: el servidor devuelve los datos ya filtrados, con su fuente y su antigüedad, y el propio asistente redacta la respuesta. Incluye además una web de apoyo con el ranking, los torneos y las noticias.

> Proyecto no oficial: sin relación con la FIP, Premier Padel, Padel API ni los medios citados.

## Conectarlo en Visual Studio Code

Necesitas VS Code con GitHub Copilot. No hace falta ninguna clave ni instalar nada más. Es un MCP público, y regulado con límites de consumo. 

1. Pulsa `Ctrl` + `Mayús` + `P` (en Mac, `Cmd` + `Mayús` + `P`), escribe `MCP: Open User Configuration` y pulsa Enter.
2. Pega esto y guarda. Las dos líneas que importan son `type` y `url`:

   ```json
   {
     "servers": {
       "padel-femenino": {
         "type": "http",
         "url": "https://mcp-padel-fem-production.up.railway.app/ai/mcp"
       }
     }
   }
   ```

3. Pulsa **Start**, que aparece encima de `"padel-femenino"`. Abre Copilot Chat, cambia al modo **Agent** y pregunta, por ejemplo: *«Dame el top 10 del ranking femenino»* o *«¿Quién ganó el último torneo?»*.

Si el archivo ya tenía otros servidores, añade solo el bloque `"padel-femenino"` dentro de `"servers"`. Si no conecta, comprueba que la dirección termina en `/ai/mcp` y mira **MCP: List Servers → Show Output**.

## Qué hace cada herramienta

El servidor ofrece **23 herramientas** de solo lectura, todas de pádel femenino. Las marcadas con ⭐ son las 3 básicas, que siempre están; las otras 20 aparecen cuando el servidor está en modo completo, que decide quien lo administra.

### Torneos

| | Herramienta | Qué hace |
|---|---|---|
| ⭐ | `proximo_torneo` | El siguiente torneo: ciudad, fechas, días que faltan y dónde verlo |
| ⭐ | `ultimo_torneo` | El último torneo terminado y su final: primeras, segundas, terceras y resultado |
| | `calendario` | Los próximos torneos |
| | `torneo_actual` | El torneo en juego y el enlace al marcador oficial |
| | `partidos_en_directo` | Lo que se juega hoy en el torneo en curso, con el marcador por sets |
| | `cuadro_torneo` | Cuartos, semifinales y final de un torneo |
| | `resultado_torneo` | La final de un torneo ya jugado |
| | `resultados_recientes` | Las últimas finales, o las de una jugadora concreta |
| | `ediciones_torneo` | Las ediciones anteriores de un torneo y sus campeonas |

### Jugadoras

| | Herramienta | Qué hace |
|---|---|---|
| | `jugadora` | Ficha de una jugadora del Top 20: su puesto, sus finales y sus noticias |
| | `perfil_jugadora` | Ficha de cualquier jugadora: país, Elo, altura, lado de la pista, mano y edad |
| | `partidos_jugadora` | Sus últimos partidos, con rival y resultado |
| | `parejas_jugadora` | Con quién ha jugado y desde cuándo |
| | `comparar_jugadoras` | Dos jugadoras del Top 20 lado a lado |
| | `prediccion_partido` | La probabilidad de victoria entre dos parejas (o dos jugadoras) |

### Rankings

| | Herramienta | Qué hace |
|---|---|---|
| ⭐ | `ranking_femenino` | El ranking oficial: posición, nombre, país y puntos (el top 10 por defecto, hasta 20) |
| | `ranking_elo` | La clasificación por Elo |
| | `ranking_race` | La clasificación RACE, distinta del ranking oficial |
| | `rankings_jugadora` | El puesto de una jugadora en los tres rankings |
| | `movimientos_ranking` | Quién sube o baja más esta semana |

### Noticias y estado

| | Herramienta | Qué hace |
|---|---|---|
| | `noticias` | Noticias de pádel femenino con un resumen de unas tres líneas, su fecha y el enlace a la noticia |
| | `resumen_del_dia` | Un panorama rápido: el top 5, el torneo en juego, la última final y 3 noticias |
| | `estado_de_los_datos` | De dónde sale cada dato y de cuándo es |

## Ventajas del MCP

**MCP** (*Model Context Protocol*) es un estándar abierto para que un asistente de IA use herramientas y datos de fuera. El servidor publica una lista de herramientas con su descripción; el asistente la lee, decide cuál sirve para tu pregunta, la llama y redacta la respuesta con lo que le devuelve.

- **Datos reales asegurados, no inventados.** Un modelo puede no saber con certeza quién lidera el ranking esta semana ni quién ganó ayer, podría inventarselos o cometer errores en la respuesta. Con el MCP consultamos los datos en fuentes válidas y verificadas en el momento y en cada respuesta podemos saber de dónde viene el dato y de cuándo es. Si falta algo, lo dice.
- **El servidor controla lo que sale**:. El asistente solo decide qué pedir y cómo contarlo. Gracias a esto, obtenemos una respuesta personalizada y orientada a nuestros gustos y requerimientos. 
- **Una web para personas y un MCP para asistentes.** Comparten los mismos datos guardados y las mismas noticias. 

## Flujo y arquitectura

```text
    NAVEGADOR (personas)                  VS CODE + COPILOT (el modelo)
              │ abren la web y piden /api/…             │ preguntan en lenguaje normal
              │                                         │ el modelo elige una herramienta
              ▼                                         ▼
┌────────────────────────────── UN SOLO SERVIDOR (Railway) ──────────────────────────────┐
│ LA WEB  (/ y /api/…)                      EL MCP  (/ai/mcp · 23 herramientas)          │
│ pinta el ranking, los torneos,            devuelve datos ya filtrados, con             │
│ las noticias y Sponsors                   su fuente y su antigüedad                    │
│             └────────────────────┬────────────────────┘                                │
│                                  ▼                                                     │
│ LOS DOS LEEN LOS MISMOS DATOS (con memoria: 60 s; las noticias, 10 min):               │
│ 1) Archivos: ranking, calendario y finales ◀─ tarea diaria (24 h) ◀─ Padel API         │
│ 2) Noticias ◀─ feeds RSS de los medios                                                 │
│ 3) Padel API en el momento (solo el MCP, 11 herramientas), con memoria y límites       │
└────────────────────────────────────────────────────────────────────────────────────────┘
              ▲ la web muestra los datos                │ datos con fuente y fecha
              │                                         ▼
      la persona los ve                         el modelo redacta ──▶ la persona
```

**La web (personas).** 1) Abres la web. 2) La página pide sus datos al servidor (`/api/ranking`, `/api/news`…). 3) El servidor responde con lo que tiene guardado en memoria. 4) La página los pinta.

**El modelo y el MCP (asistentes).** 1) Preguntas en Copilot. 2) El modelo lee la lista de herramientas y elige una. 3) El servidor la ejecuta: lee sus datos, las noticias o pregunta a Padel API. 4) Devuelve datos ya filtrados, con su fuente y su fecha. 5) El modelo redacta la respuesta.

| Dato | Quién lo pide a la fuente | Cuánto se reutiliza |
|---|---|---|
| Ranking, calendario y finales | Una tarea diaria, aunque nadie pregunte | Se guardan en archivos; la lectura se recuerda 60 s |
| Noticias | El servidor, al preguntar | 10 min |
| Padel API en el momento (11 herramientas) | El servidor, al preguntar | De 2 min a 6 h, según el dato |

**Límites de uso.** Para que cualquiera pueda usarlo sin agotar la fuente, el servidor limita las peticiones: por visitante, en total y hacia Padel API. Si se llega al límite, la herramienta dice cuánto esperar.

<details>
<summary><strong>Referencia técnica (para quien mantiene el proyecto)</strong></summary>

## Las herramientas del MCP

Solo lectura. Hay **23**: 3 por defecto y 20 extra. En la columna «Parámetros», `?` marca un parámetro opcional y `=` su valor por defecto; `—` significa que no lleva ninguno.

### Por defecto (3)

| Herramienta | Parámetros | Qué devuelve |
|---|---|---|
| `ranking_femenino` | `top?=10` | Posición, nombre, país y puntos de cada jugadora, de la 1.ª a la `top` (por defecto el top 10, máximo 20) |
| `ultimo_torneo` | — | El último torneo terminado: dónde fue, qué tipo de torneo es, quién jugó la final, primeras, segundas y terceras, resultado final y una frase que lo resume |
| `proximo_torneo` | — | El próximo torneo: nombre, ciudad y país, fechas, días que faltan, tipo de torneo y dónde verlo; si hay uno en juego, también ese |

### Extra con datos guardados por la tarea diaria (9)

Apagadas por defecto (No obstante, comentar que actualmente se encuentran activadas**): se activan con `MCP_TOOLS=extended`. Leen los archivos de `data/` que la tarea de actualización renueva cada 24 horas; no consultan a Padel API al preguntar.

| Herramienta | Parámetros | Para qué |
|---|---|---|
| `jugadora` | `nombre` | Ficha de una jugadora del Top 20: ranking, finales jugadas y noticias que la mencionan |
| `comparar_jugadoras` | `jugadora_a`, `jugadora_b` | Dos jugadoras del Top 20 lado a lado |
| `torneo_actual` | — | Torneo en juego y enlace al marcador oficial |
| `resultado_torneo` | `torneo?`, `jugadora?` | Ficha de la final femenina de cualquier torneo ya jugado (por nombre o por jugadora) |
| `resultados_recientes` | `jugadora?`, `limite?=5` | Finales femeninas ya jugadas, con filtro opcional por jugadora |
| `calendario` | `limite?=6` | Próximos torneos |
| `noticias` | `jugadora?`, `limite?=5` | Noticias de pádel femenino con un resumen de unas tres líneas, su fecha real y el enlace directo a la noticia, con filtro opcional por jugadora |
| `resumen_del_dia` | — | Panorama rápido: top 5, torneo en juego, última final y 3 noticias |
| `estado_de_los_datos` | — | Fuente, antigüedad y última actualización de cada dato |

### Extra que consultan a Padel API en el momento (11)

También con `MCP_TOOLS=extended`. Piden el dato a Padel API cuando alguien lo pregunta, con caché y presupuesto (ver «Consultas en directo a Padel API», más abajo). Solo usan lo gratuito de su plan.

| Herramienta | Parámetros | Para qué |
|---|---|---|
| `perfil_jugadora` | `nombre` | Ficha de cualquier jugadora (no solo del Top 20): país, puesto, puntos, Elo, altura, lado, mano, edad |
| `partidos_jugadora` | `nombre`, `limite?=5` | Sus últimos partidos (1 a 10): ronda, pareja, rival, resultado y si ganó |
| `cuadro_torneo` | `torneo?`, `ronda?` | Cuartos, semifinales y final del cuadro femenino de un torneo (por nombre o ciudad; por defecto el actual o el último); `ronda`: final, semifinales, cuartos u octavos |
| `partidos_en_directo` | — | Qué se juega hoy en el torneo en curso: en juego con su marcador por sets, terminados y próximos |
| `rankings_jugadora` | `nombre` | Su puesto y puntos en el ranking oficial, el RACE y el Elo, y lo que han cambiado en la última semana |
| `prediccion_partido` | `pareja_a`, `pareja_b` | Probabilidad de victoria entre dos parejas (o dos jugadoras): una lista de una o dos jugadoras por lado, con el Elo de Padel API |
| `ranking_elo` | `top?=10` | Clasificación femenina por Elo (de la 1.ª a la `top`, máximo 20) |
| `ranking_race` | `top?=10` | Clasificación femenina RACE (de la 1.ª a la `top`, máximo 20), con sus cambios de la semana |
| `movimientos_ranking` | `tipo?=oficial`, `sentido?=suben`, `top?=5` | Las jugadoras que más suben o bajan esta semana; `tipo`: oficial, race o elo; `sentido`: suben o bajan |
| `parejas_jugadora` | `nombre` | Con quién ha jugado una jugadora, desde y hasta cuándo |
| `ediciones_torneo` | `torneo?` | Ediciones anteriores de un torneo con sus campeonas, fechas, ciudad y premio |

Cada respuesta indica su fuente, su antigüedad y los avisos (datos de ejemplo, datos con retraso). Cuando falta un dato lo dice en una nota en vez de inventarlo: por ejemplo, las terceras (las semifinalistas, porque en pádel no se juega partido por el tercer puesto) solo se dan si la fuente aporta las semifinales. Ni la web ni las herramientas por defecto tienen marcadores en directo (se enlaza al marcador oficial); `partidos_en_directo`, que es extra, da el marcador por sets del torneo en curso, con algo de retraso. En un torneo o una jugadora vale una parte del nombre o de la ciudad («Milano», «Triay»).

## Memoria de cada herramienta y consumo de Padel API

### La memoria de cada herramienta

«Dura en memoria» es cuánto tiempo se reutiliza lo que ya se pidió antes de volver a pedirlo.

| Herramienta | Lee de | Dura en memoria |
|---|---|---|
| `ranking_femenino` | `ranking.json` | 60 s |
| `ultimo_torneo` | `calendario.json` (finales) | 60 s |
| `proximo_torneo` | `calendario.json` | 60 s |
| `jugadora` | `ranking.json`, finales y noticias (estas con su propia memoria de 10 min) | 60 s |
| `comparar_jugadoras` | `ranking.json` | 60 s |
| `torneo_actual` | `calendario.json` | 60 s |
| `resultado_torneo` | `calendario.json` (finales) | 60 s |
| `resultados_recientes` | `calendario.json` (finales) y `ranking.json` | 60 s |
| `calendario` | `calendario.json` | 60 s |
| `noticias` | Feeds RSS de los medios | 10 min |
| `resumen_del_dia` | Ranking, calendario, finales y noticias (estas con su memoria de 10 min) | 60 s |
| `estado_de_los_datos` | El estado de los archivos y de la tarea; se calcula al momento | — |
| `perfil_jugadora` | Padel API: ficha de la jugadora | 6 h |
| `partidos_jugadora` | Padel API: partidos de la jugadora | 15 min |
| `cuadro_torneo` | Padel API: partidos del torneo | 2 min |
| `partidos_en_directo` | Padel API: partidos del torneo | 2 min |
| `rankings_jugadora` | Padel API: rankings de la jugadora | 1 h |
| `prediccion_partido` | Padel API: simulación del partido | 1 h |
| `ranking_elo` | Padel API: rankings | 1 h |
| `ranking_race` | Padel API: rankings | 1 h |
| `movimientos_ranking` | Padel API: rankings | 1 h |
| `parejas_jugadora` | Padel API: parejas de la jugadora | 6 h |
| `ediciones_torneo` | Padel API: ediciones del torneo | 6 h |

Cómo funciona esa memoria:

- **El plazo cuenta desde que se guardó**, no desde la última pregunta: volver a preguntar no lo alarga.
- **Pasado el plazo no se renueva solo.** La siguiente pregunta es la que vuelve a pedir el dato. Lo único que se renueva por su cuenta es el camino 1, con la tarea diaria.
- **Cada petición distinta es otro dato**: otro torneo, otra ronda o `limite=6` en vez de `limite=5` no aprovechan lo anterior.
- **Varias preguntas a la vez** por el mismo dato hacen una sola petición.
- **Un dato que el plan no incluye** se recuerda 30 min, y uno que no existe, 2 min, para no insistir.
- **Una jugadora fuera del Top 20** se busca por su nombre en Padel API, y esa búsqueda se recuerda 6 h. Las del Top 20 se resuelven con lo guardado, sin ninguna petición.
- **Tras un reinicio o un despliegue la memoria queda vacía**, y en Railway el disco también: los datos reales tardan unos 4 minutos en volver y, mientras tanto, se ven los de ejemplo.

### Cuánto consume de Padel API

- **La tarea diaria** hace unas 3 peticiones al día (ranking femenino, ranking masculino —solo para filtrar noticias— y calendario) y 2 más por cada torneo terminado nuevo. La primera pasada tras desplegar hace unas veinte, con pausa entre ellas.
- **Las preguntas** a las herramientas de Padel API tienen un presupuesto propio de **5 por minuto y 600 por día**, muy por debajo del límite gratuito (10 por minuto y 2.000 al día). Una pregunta nueva cuesta una o dos peticiones y repetirla enseguida cuesta cero. Si el presupuesto se agota, la herramienta dice cuánto esperar y nunca se bloquea.
- El consumo se ve en `/api/data-status`, en `consultas_en_directo`.

## De dónde salen los datos

| Dato | Fuente |
|---|---|
| Ranking, calendario y resultados | **Padel API** (padelapi.org) si hay `PADELAPI_TOKEN`; si no hay token, o falla y todavía no hay datos buenos de la API guardados, la API de **Wikipedia** (página *Premier Padel <año>*, CC BY-SA 4.0) |
| Noticias | Los **feeds RSS** de los medios, con una regla por fuente (ver abajo) |
| Marcadores en directo | No se reproducen: enlace al marcador oficial |
| «Sponsors» y «¿Sabías que?» | Textos escritos a mano en `static/index.html`, sin fuente automática |

**Padel API.** Servicio comercial con datos de Premier Padel (Major, P1, P2 y Finals) y un plan gratuito: ranking mundial actual, calendario y resultados de los últimos 6 meses, 50.000 peticiones al mes (10 por minuto y 2.000 al día), con cuadros completos desde 2023, de donde salen las semifinalistas. La tarea diaria hace unas pocas peticiones con una pausa de 6,5 s entre ellas y no vuelve a pedir un torneo ya completado. Sus [condiciones](https://padelapi.org/terms-of-service) permiten usar los datos en tu propia aplicación y guardar las respuestas; **no** permiten revenderlos como producto, copiar su base de datos ni extraerla en masa. Por eso el MCP ofrece respuestas filtradas y limitadas, y no un paso directo de su API. Sus nombres vienen con los dos apellidos y sin tildes («Gemma Triay Pons»): se guardan como nombre y primer apellido («Gemma Triay»), que es como los escribe la prensa.

**Wikipedia (respaldo).** La mantienen voluntarios, así que el ranking y los resultados pueden ir con **semanas de retraso**. Solo recoge ganadoras y finalistas, sin semifinales. La web y el MCP avisan cuando los datos pasan de 14 días.

**Noticias.** Se consultan como mucho cada 10 minutos, solo se publican las que hablan de alguna jugadora del Top 20 y como máximo 15. **Nunca de hombres**: manda el titular. Si el titular nombra a un jugador, o dice «masculino», la noticia se descarta; un titular solo con apellidos no basta (muchos son también de hombres) y hace falta que diga expresamente que es femenino. Para saber quiénes son ellos, la tarea de actualización guarda también el ranking masculino de Padel API (`data/ranking_masculino.json`, solo nombres: no se muestra en ningún sitio). A esa lista se suma siempre una lista fija de jugadores muy conocidos y de sus apodos (Coello, Tapia, Chingotto, «Stupa»…), para que no se cuele ninguno aunque la API falle o aún no haya descargado la suya. Sin la lista de la API (por ejemplo con el respaldo de Wikipedia, o unos minutos tras un despliegue) el filtro es más estricto: solo pasan las noticias que digan expresamente femenino, mujeres, campeonas o jugadoras. Cada medio se trata según lo que permiten sus condiciones; cualquier fuente nueva empieza en «solo titular y enlace» hasta que se lean las suyas:

Cada noticia que se publica lleva su **fecha real** (la que da el medio; si no la da, no se inventa), un **resumen de al menos unas tres líneas** y el **enlace directo** a la noticia. Una noticia sin ese resumen no sale. Por eso solo se publican las fuentes cuyas condiciones permiten resumir o citar; cualquier otra se ignora (ni siquiera se le pide el feed) hasta que se lean las suyas:

## Estructura del proyecto

```text
.
├── README.md
├── railway.toml           # indica a Railway qué Dockerfile usar
├── docker-compose.yml     # configuración de Docker
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
    │   ├── padelapi_live.py     # consultas a Padel API en el momento (herramientas extra), con caché y presupuesto
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
GET /api/version               versión desplegada (también en /api/data-status y al pie de la web)
GET /como-funciona             la arquitectura explicada para todos
```

FastAPI añade además `/docs`, `/redoc` y `/openapi.json` (ver las limitaciones).

## Uso responsable y licencias

- El programa se identifica como `PadelFemBot/1.0`, respeta `robots.txt` en los feeds y espera entre peticiones al mismo dominio. A Wikipedia le pide una página al día.
- Wikipedia: CC BY-SA 4.0; la web cita la fuente al pie. Padel API: sus condiciones, arriba. OKDiario exige **atribución con enlace directo, sin `nofollow`**, a cada artículo; la web ya lo hace. Conviene releer las condiciones de vez en cuando.
- La pestaña «Sponsors» no contiene datos de contratos y avisa de que el material no está verificado con las marcas. Si algún día cobras de una marca o añades enlaces de afiliación, pasa a ser publicidad y las fuentes con licencia no comercial (OKDiario) dejarían de valer.
- El historial de git de un repositorio que haya contenido versiones anteriores puede conservar código antiguo de scraping. Si vas a hacerlo público y eso te preocupa, empieza un repositorio nuevo.

Si eres titular de una web usada como fuente y prefieres que no se acceda a ella, escribe al contacto del repositorio y se retirará.

## Limitaciones conocidas

- **Retraso y alcance.** Con Padel API gratuita solo hay resultados de los últimos 6 meses. Con el respaldo de Wikipedia, semanas de retraso y sin terceras. Solo se publican las finales femeninas, no las rondas previas ni marcadores en directo.
- **Arranque.** El disco de Railway es efímero: tras cada despliegue se vuelven a pedir los datos y durante unos 3-4 minutos se muestran los de ejemplo.
- **Noticias.** Hay pocas (los medios publican unas pocas de pádel a la semana), como máximo 15, y la mayoría solo con titular y enlace.
- **Superficie pública.** FastAPI publica `/docs`, `/redoc` y `/openapi.json`. El MCP en modo abierto no identifica a nadie: para uso público, `MCP_AUTH=github`.
- **Consultas en directo.** Cada pregunta distinta de las once herramientas cuesta una o dos peticiones a Padel API (el Top 20 se resuelve sin peticiones). El presupuesto las limita, pero un uso muy intenso puede agotarlo; sube `PADELAPI_LIVE_PER_MIN` y `PADELAPI_LIVE_PER_DAY` con cuidado. No se han probado con un token real: la búsqueda de una jugadora fuera del Top 20 por su nombre, los cuadros del torneo en curso, la ordenación de los rankings por puntos ganados y los estados de las parejas se han escrito a partir de la documentación.
- **Padel API.** Si el MCP se hace muy público, conviene confirmar con ellos que su uso encaja con sus condiciones.

## Objetivo

Caso práctico para combinar **APIs, fuentes abiertas, procesamiento asíncrono, IA y Model Context Protocol (MCP)** en una aplicación real.

</details>
