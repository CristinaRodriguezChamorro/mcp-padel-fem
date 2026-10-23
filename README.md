# 🎾 MCP Pádel Femenino

Servidor **MCP** con datos reales del pádel profesional femenino (circuito Premier Padel / FIP). Cualquier asistente de IA que hable MCP (Copilot en VS Code, Claude, etc.) puede llamar a sus herramientas: el servidor devuelve los datos ya filtrados, con su fuente y su antigüedad, y el propio asistente redacta la respuesta. Además publica una web sencilla, que es un apartado aparte (ver «La web»).

> Proyecto no oficial: sin relación con la FIP, con Premier Padel, con Padel API ni con los medios citados.

## Qué puedes consumir

El servidor ofrece **23 herramientas** de solo lectura, todas de pádel femenino. La mayoría de la gente las usa desde VS Code con la **URL pública** del servidor: `https://TU-APP.up.railway.app/ai/mcp` (sustituye `TU-APP` por el dominio del despliegue).

| Grupo | Nº | Qué te dan | De dónde salen los datos | Cómo se activan |
|---|---|---|---|---|
| Por defecto | 3 | Ranking, último torneo y próximo torneo | Archivos que una tarea renueva cada 24 h | Siempre |
| Extra con datos guardados | 9 | Fichas del Top 20, comparar jugadoras, resultados, calendario, noticias, resumen del día, estado de los datos | Esos mismos archivos y las noticias de los medios | `MCP_TOOLS=extended` en el servidor |
| Extra en el momento | 11 | Cualquier jugadora, sus partidos y parejas, cuadros y ediciones de torneos, rankings Elo y RACE, quién sube o baja, predicciones | Padel API, al preguntar (con memoria en el servidor) | `MCP_TOOLS=extended` en el servidor |

**Cuántas herramientas ves depende del servidor, no de tu VS Code:** si el servidor no tiene `MCP_TOOLS=extended`, solo verás las 3 por defecto. El catálogo completo, con los parámetros de cada una, está en «Las herramientas del MCP».

## Conectarlo desde VS Code (Copilot)

Necesitas una versión reciente de VS Code con GitHub Copilot Chat y su **modo Agent**, que es el que puede usar servidores MCP. Si usas Copilot de una empresa, su administrador puede tener los servidores MCP desactivados.

### Opción A: el servidor público (la habitual)

No instalas nada: el servidor ya está en marcha y solo necesitas su dirección.

1. Pide la URL pública del servidor. Tiene la forma `https://TU-APP.up.railway.app/ai/mcp` (termina siempre en `/ai/mcp`) y te dirán si exige un token de GitHub.
2. Crea el archivo `.vscode/mcp.json` en cualquier carpeta que tengas abierta. Para tenerlo en todos tus proyectos, abre la paleta de comandos (Ctrl/Cmd + Mayús + P), ejecuta **MCP: Open User Configuration** y pega lo mismo ahí.
3. Pega esta configuración con tu dirección (servidor abierto, sin token):

   ```json
   {
     "servers": {
       "padel-femenino": {
         "type": "http",
         "url": "https://TU-APP.up.railway.app/ai/mcp"
       }
     }
   }
   ```

4. Pulsa **Start** sobre el servidor en ese archivo (o ejecuta **MCP: List Servers** y arráncalo desde ahí).
5. Abre Copilot Chat, cambia al modo **Agent** y mira el icono de herramientas: debe aparecer `padel-femenino` con sus herramientas (3 o 23, según cómo esté configurado el servidor).
6. Pregunta, por ejemplo: *«Dame el top 10 del ranking femenino con sus nombres y sus puntos»*, *«¿Quién ganó el último torneo y por cuánto?»* o, con las 23, *«¿Quién gana, Triay y Brea contra Sánchez y González?»*. Despliega la llamada en el chat para ver lo que devolvió el servidor.

**Si el servidor exige un token de GitHub** (`MCP_AUTH=github`), usa `examples/vscode-mcp.json` en lugar de lo anterior. VS Code te pide el token al arrancar, con la entrada oculta, y lo envía en la cabecera `Authorization`:

```json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "github-token",
      "description": "Token de GitHub (sin permisos) para el MCP de pádel femenino",
      "password": true
    }
  ],
  "servers": {
    "padel-fem": {
      "type": "http",
      "url": "https://TU-APP.up.railway.app/ai/mcp",
      "headers": {
        "Authorization": "Bearer ${input:github-token}"
      }
    }
  }
}
```

El token de GitHub no necesita **ningún permiso**: crea uno solo de identidad (GitHub → Settings → Developer settings → Personal access tokens) y con caducidad corta.

### Opción B: tu propio servidor, en local

Sirve para probar cambios o para no depender del servidor público. Necesitas Python 3.12 (vale 3.11).

1. Arranca el servidor desde `apps/mcp-server`:

   ```bash
   cd apps/mcp-server
   python -m venv .venv
   source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   export MCP_TOOLS=extended          # PowerShell: $env:MCP_TOOLS="extended"  (sin esto, solo las 3 por defecto)
   export PADELAPI_TOKEN="tu-token"   # PowerShell: $env:PADELAPI_TOKEN="tu-token"  (opcional)
   python server.py
   ```

2. Espera unos 3-4 minutos la primera vez y abre <http://localhost:8000/api/data-status>: `ingesta.ok` debe ser `true` y `fuente_activa` `padelapi`. Hasta entonces se muestran los datos de ejemplo.
3. Crea `.vscode/mcp.json` con esto (es `examples/vscode-mcp-local.json`) y pulsa **Start**:

   ```json
   {
     "servers": {
       "padel-femenino": {
         "type": "http",
         "url": "http://localhost:8000/ai/mcp"
       }
     }
   }
   ```

El token de [Padel API](https://padelapi.org) es gratuito y **opcional**: sin él se usa Wikipedia, con más retraso. Va **solo** como variable de entorno, nunca en un archivo ni en el repositorio. La actualización reescribe `data/ranking.json` y `data/calendario.json`: no los subas a git (`git checkout -- apps/mcp-server/data` los restaura). También puedes arrancarlo con Docker: `docker compose up --build`.

### Público o local

| | Público | Local |
|---|---|---|
| Dirección | `https://TU-APP.up.railway.app/ai/mcp` | `http://localhost:8000/ai/mcp` |
| Quién lo arranca | Ya está en marcha | Tú, en tu ordenador (debe seguir encendido) |
| Herramientas | Las que active el servidor (3 o 23) | Las que actives tú con `MCP_TOOLS` |
| Acceso | Abierto o con token de GitHub, según el servidor | Sin acceso: solo desde tu ordenador |
| Token de Padel API | Lo tiene el servidor | Opcional, el tuyo |

### Si algo falla

Abre **MCP: List Servers**, elige el servidor y pulsa **Show Output**: ahí está el mensaje real.

| Síntoma | Causa más probable | Qué hacer |
|---|---|---|
| Error 404 | La ruta está mal escrita (`/ia/mcp` en vez de `/ai/mcp`) | Corrige la dirección: acaba en `/ai/mcp` |
| No conecta desde otro ordenador | Usas `localhost`, que es siempre el ordenador donde escribes | Usa la dirección pública del servidor |
| No conecta en local | Pusiste `https://` con `localhost` | Es `http://localhost:8000/ai/mcp` |
| No sale ninguna herramienta | No pulsaste **Start**, o no estás en modo **Agent** | Arranca el servidor y cambia a Agent |
| Solo salen 3 herramientas | El servidor no tiene `MCP_TOOLS=extended` | Pídeselo a quien lo administra, o actívalo si es tuyo |
| Errores 401 o 403 | El servidor exige token de GitHub | Usa `examples/vscode-mcp.json` con un token válido |
| La dirección abierta en el navegador da error 405 | Es normal: no está hecha para abrirse en una página | Compruébalo abriendo `/api/version` en su lugar |
| Cambiaste algo del servidor y no lo ve | VS Code guarda la lista de herramientas | **MCP: List Servers** → reiniciar el servidor |

**Tu estilo, en tus instrucciones.** El servidor decide qué datos devuelve y el agente cómo contarlos; el formato se pide en las instrucciones de tu cliente. Por ejemplo, en `.github/copilot-instructions.md`:

```text
Cuando pregunte por pádel femenino, usa el MCP.
Responde en español, breve y con una tabla si hay varias jugadoras.
Si los datos tienen retraso, dímelo en una línea.
No inventes datos que el MCP no devuelva.
```

## Las herramientas del MCP

Solo lectura. Hay **23**: 3 por defecto y 20 extra. En la columna «Parámetros», `?` marca un parámetro opcional y `=` su valor por defecto; `—` significa que no lleva ninguno.

### Por defecto (3)

| Herramienta | Parámetros | Qué devuelve |
|---|---|---|
| `ranking_femenino` | `top?=10` | Posición, nombre, país y puntos de cada jugadora, de la 1.ª a la `top` (por defecto el top 10, máximo 20) |
| `ultimo_torneo` | — | El último torneo terminado: dónde fue, qué tipo de torneo es, quién jugó la final, primeras, segundas y terceras, resultado final y una frase que lo resume |
| `proximo_torneo` | — | El próximo torneo: nombre, ciudad y país, fechas, días que faltan, tipo de torneo y dónde verlo; si hay uno en juego, también ese |

### Extra con datos guardados por la tarea diaria (9)

Apagadas por defecto: se activan con `MCP_TOOLS=extended`. Leen los archivos de `data/` que la tarea de actualización renueva cada 24 horas; no consultan a Padel API al preguntar.

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

## Cómo funciona por dentro

### El flujo completo

```text
                Tú ──pregunta──▶ Copilot (el agente)
                                          │  elige una herramienta
                                          ▼
                             MCP del servidor (/ai/mcp)
                                          │
             ┌────────────────────────────┴────────────────────────────┐
             ▼                            ▼                            ▼
Archivos guardados           Padel API, en el             Feeds de los medios,
(carpeta data/): los         momento, con memoria         con memoria en el
renueva cada 24 h una        en el servidor               servidor
tarea de fondo
◀── Padel API
             └────────────────────────────┬────────────────────────────┘
                                          ▼
                  datos ya filtrados, con su fuente y su antigüedad
                                          ▼
                         Copilot redacta la respuesta ──▶ Tú
```

1. **Preguntas** en lenguaje normal en Copilot, en modo Agent.
2. **El agente elige** la herramienta y sus parámetros leyendo sus descripciones y las instrucciones del servidor.
3. **El servidor la ejecuta**: según la herramienta, lee archivos guardados, pregunta a Padel API en ese momento o lee las noticias.
4. **Devuelve datos ya recortados y filtrados** (solo pádel femenino, sin hombres en las noticias), con su fuente, su antigüedad y los avisos. Si algo falla, el error llega con un tipo (`plan`, `limit`, `budget`, `notfound`, `invalid`, `config`, `network`, `upstream` o `auth`) y el agente se lo explica a la persona.
5. **El agente redacta la respuesta.** El servidor no decide el formato: solo qué datos.

### Tres caminos para los datos

| Camino | Herramientas | Quién pide los datos y cuándo | Dónde se guardan |
|---|---|---|---|
| **1. Archivos guardados** | Las 3 por defecto, `jugadora`, `comparar_jugadoras`, `torneo_actual`, `resultado_torneo`, `resultados_recientes`, `calendario`, `resumen_del_dia` y `estado_de_los_datos` | Una tarea de fondo, **cada 24 h**, aunque nadie pregunte. Si falla, reintenta a la hora, a las 2, a las 4 y después cada 6 h | Archivos JSON en `data/`, en el disco del servidor, más una capa de 60 s en memoria al leerlos |
| **2. Noticias** | `noticias`, y la parte de noticias de `jugadora` y `resumen_del_dia` | El servidor, **al preguntar**, si pasaron 10 min | Memoria del servidor: 10 min el resultado, cada feed con su control de cambios y 24 h los resúmenes con IA |
| **3. Padel API en el momento** | Las 11 extra de Padel API | El servidor, **al preguntar**, si ese dato no está en memoria o caducó | Solo memoria del servidor (no se escribe nada en archivos) |

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

| Nivel | Qué se publica | Medios |
|---|---|---|
| resumen | Título, resumen propio de unas tres líneas (con IA si hay `GROQ_API_KEY`; si el modelo de Groq ya no existe se busca otro solo, y si no hay ninguno, las primeras frases de la crónica), fecha, autor y enlace. Usa el texto completo que OKDiario publica en su feed | OKDiario (licencia Creative Commons no comercial) |
| extracto | Título, extracto literal de unas tres líneas (hasta 300 caracteres, sin reescribir), fecha y enlace | Padel Addict |

Marca, Mundo Deportivo, Padel Spain y Set Padel Magazine se han dejado fuera: no se han verificado sus condiciones para resumir sus noticias, y sin eso no se les puede dar el resumen que se exige.

**Consultas en directo a Padel API.** Las once herramientas extra que no leen archivos (`perfil_jugadora`, `partidos_jugadora`, `cuadro_torneo`, `partidos_en_directo`, `rankings_jugadora`, `prediccion_partido`, `ranking_elo`, `ranking_race`, `movimientos_ranking`, `parejas_jugadora` y `ediciones_torneo`) piden el dato a Padel API **cuando alguien lo pregunta**. Solo usan lo **gratuito** de su plan, y se protegen y protegen a Padel API:

- **Caché**: el mismo dato no se vuelve a pedir hasta que caduca (perfiles, parejas y ediciones 6 h; partidos 15 min; cuadros 2 min; rankings y predicciones 1 h).
- **Una sola petición** por dato aunque lleguen varias preguntas a la vez.
- **Presupuesto propio**: `PADELAPI_LIVE_PER_MIN` (5 por minuto) y `PADELAPI_LIVE_PER_DAY` (600 por día), muy por debajo del límite gratuito (10 por minuto y 2.000 al día), para dejar sitio a la actualización diaria. Si se agota, la herramienta dice cuánto esperar; nunca se bloquea. `/api/data-status` muestra el consumo en `consultas_en_directo`.
- **Lo que el plan no incluye** (error 402) se explica y se recuerda 30 minutos, sin insistir. Las redirecciones de Padel API (cuando fusiona dos registros) se siguen solas.
- Solo **pádel femenino** y respuestas recortadas a lo necesario: nunca se devuelve en bruto su base de datos.

Límites del plan gratuito, según su documentación (sin probar con un token real): los resultados de partidos de más de 6 meses salen ocultos (`hidden_free_plan`), solo hay cuadro principal, y de los rankings solo se ve la última semana. Una herramienta nunca llama a un endpoint de pago; un test lo vigila. **Lo de pago que no se usa**: estadísticas de jugadora y de pareja (plan Pro), forma reciente y sinergia (Business), el directo punto a punto (`/live`, Pro), el histórico semanal del ranking, el índice de interés de los partidos y el cara a cara. `partidos_en_directo` usa los partidos del torneo en curso: el marcador es por sets y puede ir con unos minutos de retraso.

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
   - `MCP_TOOLS`: `extended` si quieres ofrecer las 23 herramientas (sin esto, solo las 3 por defecto).
   - `SCRAPER_CONTACT`: tu correo, para identificar al programa ante los medios.
4. En **Settings → Networking → Generate Domain** obtén la dirección pública.
5. Espera unos 4 minutos tras el arranque y abre `https://TU-APP.up.railway.app/api/data-status`: `ingesta.ok` debe ser `true` y `fuente_activa` `padelapi`. El disco de Railway es efímero: tras cada despliegue los datos se vuelven a pedir.
6. El MCP queda en `https://TU-APP.up.railway.app/ai/mcp`: esa es la **URL pública** que se comparte para conectarlo desde VS Code (ver «Conectarlo desde VS Code»).
7. Para saber qué versión está corriendo, abre `https://TU-APP.up.railway.app/api/version` (también sale al pie de la web): si no es la última que has subido, el despliegue no ha terminado o no ha cogido el último commit.

Si el despliegue falla, el log de Railway dice dónde. Si se queja de no encontrar `requirements.txt` al construir, el contexto de construcción no es `apps/mcp-server`: revisa el *Root Directory* del servicio.

## La web

Además del MCP, el mismo servidor publica una web sencilla en la raíz (`/`), pensada como apoyo y aparte de las herramientas. Tiene las pestañas Noticias, En juego, Ranking, Torneos, Sponsors y ¿Sabías que?, y lee los mismos archivos guardados y las mismas noticias que las herramientas. No usa las consultas en directo a Padel API, no muestra fotos y no tiene marcadores en directo. `/como-funciona` explica la arquitectura para todos. Las rutas están en «Endpoints de la web».

## Variables de entorno

| Variable | Para qué | Por defecto |
|---|---|---|
| `PORT` | Puerto del servidor | `8000` |
| `DATA_DIR` | Carpeta de los archivos de datos | `apps/mcp-server/data` |
| `PADELAPI_TOKEN` | Token de Padel API: activa la fuente principal (¡solo como variable del servidor!) | — |
| `PADELAPI_INGEST` | `off` desactiva Padel API aunque haya token | activa |
| `PADELAPI_LIVE_PER_MIN` | Consultas en directo a Padel API por minuto (herramientas extra) | `5` |
| `PADELAPI_LIVE_PER_DAY` | Consultas en directo a Padel API por día (herramientas extra) | `600` |
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
| `NEWS_RSS_FEEDS` | Sustituye la lista de feeds (URLs separadas por comas); los que no estén en la tabla de arriba se ignoran | los de la tabla |
| `GROQ_API_KEY` | Resúmenes de noticias con IA (sin ella, resumen determinista) | — |
| `GROQ_NEWS_MODEL` | Modelo de Groq para los resúmenes (si ya no existe, se descubre otro solo) | `llama-3.1-8b-instant` |
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

## Pruebas

```bash
cd apps/mcp-server
python -m unittest discover -s tests -v
```

No necesitan internet ni tokens. Incluyen una barrera que falla si el código apunta a un dominio que no esté en la lista permitida o si se reintroduce una librería de scraping, y tests que comparan este README con el código (herramientas y sus parámetros, la memoria de cada una, variables de entorno, rutas, archivos, árbol de carpetas y los ejemplos de VS Code).

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
