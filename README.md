# 🎾 MCP Pádel Femenino

Proyecto personal para centralizar información del **pádel femenino profesional** y experimentar con **APIs, IA y Model Context Protocol (MCP)** sobre un caso de uso real.

La aplicación reúne **noticias, ranking, calendario de torneos y seguimiento del torneo en juego**, y expone parte de esta información mediante un servidor MCP.

## 🏗️ Arquitectura

```text
FIP · RSS · fuentes web
          │
          ▼
 Extracción de datos
 aiohttp · BeautifulSoup
 feedparser · pypdf
          │
          ▼
 Filtrado y normalización
          │
          ├──► Groq / LLM
          │    extracción de datos
          │    no estructurados
          ▼
      FastAPI
   ┌──────┴──────┐
   ▼             ▼
Frontend      MCP Server
HTML/CSS/JS   FastMCP
```

La arquitectura separa la **obtención de datos**, su **procesamiento** y la **forma de consumirlos**. El LLM se utiliza únicamente donde aporta valor editorial, como el resumen de noticias. **En juego no depende de un LLM**: FIP se procesa con Playwright/HTTP y parsers Python deterministas.

## 🔴 En juego

El backend detecta el torneo Premier Padel activo y consulta información oficial de **FIP**.

Procesa **Results, Draws y Order of Play**, filtra el cuadro femenino (`female / women`), extrae resultados y los agrupa por ronda:

```text
SEMIFINALES

Triay / Brea 🏆
Josemaría / Sánchez
6–3  6–4
```

También obtiene el **siguiente partido femenino** y convierte su hora local a **hora de España (`Europe/Madrid`)**.

Los resultados se incorporan a medida que terminan los partidos, sin esperar a que finalice el torneo.

## 📰 Noticias

El filtro de noticias **no depende de una lista de jugadoras hardcodeada**.

Primero se obtiene el **ranking femenino actualizado** y se construye una lista dinámica con los nombres completos de las jugadoras. Una noticia pasa el filtro si aparece al menos una jugadora del ranking **o** si contiene una señal explícita de contenido femenino como `women`, `female`, `woman`, `femenino`, `femenina` o `mujeres`. Después, Groq/Llama resume únicamente el contenido que ya ha superado ese filtro.

```text
Ranking femenino → nombres de jugadoras
                         │
RSS → filtro femenino ───┘
          │
          ▼
      Groq + Llama
  resumen / normalización
          │
          ▼
       /api/news
```

**Groq no decide si una noticia es femenina.** Se utiliza como API de inferencia para ejecutar Llama y generar un **titular-resumen** y una **síntesis redactada** de cada noticia que ya ha pasado el filtro. No se publican fragmentos RSS recortados como si fueran resúmenes.

## 🔌 MCP

El proyecto incorpora **Model Context Protocol** mediante FastMCP.

Esto permite que la lógica del proyecto no esté limitada a la web: un cliente de IA compatible con MCP puede descubrir y utilizar las tools expuestas por el servidor.

```text
Cliente IA → MCP → Tools → datos de pádel femenino
```

## 🧰 Stack

| Tecnología | Uso |
|---|---|
| **Python + FastAPI** | Backend y API REST |
| **FastMCP / MCP** | Tools para clientes de IA |
| **aiohttp + asyncio** | Peticiones asíncronas |
| **BeautifulSoup + feedparser** | HTML y RSS |
| **Playwright + Chromium** | Interacción con resultados FIP renderizados por JavaScript |
| **pypdf** | Documentos oficiales FIP |
| **Groq API + Llama** | Inferencia LLM para resumen y normalización |
| **HTML + CSS + JavaScript** | Frontend |
| **Docker + Railway** | Contenedorización y despliegue |

## 📁 Estructura

```text
apps/mcp-server/
├── server.py          # FastAPI y endpoints
├── tools/
│   ├── news.py        # Pipeline de noticias
│   └── live_data.py   # Torneo, resultados y próximo partido
├── static/
│   └── index.html     # Frontend
├── Dockerfile
└── requirements.txt
```

Endpoints principales:

```http
GET /api/news
GET /api/live?gender=women
GET /api/ranking
GET /api/tournaments
```

## 🎯 Objetivo técnico

El proyecto explora cómo combinar **arquitectura de APIs + fuentes externas + procesamiento asíncrono + LLMs + MCP** manteniendo cada responsabilidad separada.

La IA es una pieza de la arquitectura, no la arquitectura completa.


### Objetivo de Noticias
Fecha real de publicación + titular-resumen + resumen redactado de 2–3 frases. Nunca se muestra un fragmento RSS truncado como resumen.

### Objetivo de En juego
Torneo activo + dónde verlo + próximo partido femenino en hora española + resultados femeninos finalizados agrupados por ronda.


## Contrato funcional

**Noticias**
- filtro por jugadoras del ranking femenino o términos explícitos `female / women / femenino`;
- fecha original de publicación visible en cada tarjeta;
- titular-resumen;
- resumen redactado de 2–3 frases;
- nunca extractos RSS truncados.

**En juego**
- torneo femenino activo;
- ciudad, fechas y dónde verlo;
- próximo partido femenino convertido a hora de España;
- resultados ya finalizados sin esperar al final del torneo;
- agrupados por ronda;
- ganadoras, perdedoras y marcador.


### En juego no usa Groq
La sección de resultados es determinista:

`FIP → Playwright/HTTP → parser Python → /api/live → frontend`

Los marcadores, rondas y próximo partido no dependen de cuotas ni disponibilidad de un modelo de lenguaje.


### Railway / Playwright
Chromium se instala en `/ms-playwright` mediante `PLAYWRIGHT_BROWSERS_PATH`, de forma que el usuario no-root que ejecuta la aplicación puede encontrar el navegador.

### Resiliencia de Noticias
Groq sigue generando el titular-resumen y la síntesis cuando está disponible. Si devuelve `429`, Noticias no queda vacía: se usa un fallback determinista basado únicamente en artículos cuyo titular ya es inequívocamente femenino y en frases completas del contenido filtrado.


### Noticias cuando Groq está limitado
Si Groq devuelve `429`, la web mantiene varias noticias usando un fallback determinista sobre el contenido femenino ya filtrado. Se aceptan titulares con términos femeninos, nombres completos o combinaciones de al menos dos apellidos del ranking; los titulares explícitamente masculinos se rechazan salvo que el cuerpo contenga una sección femenina clara.


### Groq es opcional
Groq se usa únicamente como mejora editorial de Noticias. Si devuelve `429`:
- se abre un circuit breaker de 30 minutos;
- no se hacen más llamadas durante ese periodo;
- Noticias continúa con fallback determinista;
- resúmenes ya generados se reutilizan desde caché durante 24 horas.

`En juego`, Ranking y Calendario no dependen de Groq.


### Número de noticias
No existe un límite fijo de 4 noticias. Se muestran todas las noticias que superan el filtro de pádel femenino, ordenadas de más reciente a más antigua.


### Parser de resultados FIP
FIP renderiza nombres y juegos en celdas separadas, no como `6-3 6-4`.
La extracción de `En juego` captura cada bloque de partido alrededor del símbolo de ganador `✓`, identifica las cuatro jugadoras mediante el ranking femenino y reconstruye el marcador set a set sin utilizar un LLM.


### Corrección v26
El fallback determinista de Noticias vuelve a incluir `_sentencias_completas`, que había desaparecido en una refactorización y provocaba `NameError` + HTTP 500. Además `/api/news` tiene protección final para que un fallo de procesamiento no tumbe la pestaña.


### Corrección v27 de En juego
El parser ya no depende de encontrar un marcador `6-3` ni de localizar el símbolo `✓` como nodo HTML. Recorre el texto realmente renderizado por FIP, identifica las jugadoras mediante el ranking femenino y reconstruye cada set a partir de las celdas numéricas situadas entre parejas.


### Corrección v28 de Female en FIP
Los logs mostraron `female=False` y `female_hits=0`: el navegador funcionaba, pero el control visual `Female` no se estaba activando. La extracción ahora prueba el control por texto, labels, inputs vecinos y radios/inputs reales. Cada intento se valida contra nombres del ranking femenino; si no aparecen al menos dos jugadoras, la vista no se considera femenina y no se parsean datos masculinos por error.


### Corrección v29 de En juego
Los logs demostraron que el selector visual `Female` de FIP no cambia de forma fiable en Chromium (`female=False`). La extracción ya no depende de ese click: inspecciona respuestas XHR/fetch y contenido DOM oculto/pre-cargado, y solo acepta una fuente si contiene varias jugadoras del ranking femenino. Después reconstruye los resultados con Python, sin Groq.


### Corrección v30 de resultados
Los logs de v29 mostraron 103 coincidencias de jugadoras pero 0 partidos parseados. Eso demuestra que la información sí está cargada, pero el orden lineal del texto no representa cada partido. v30 deja de parsear texto lineal: Playwright localiza directamente el contenedor DOM más pequeño que contiene 4 jugadoras del ranking y 4–8 celdas de marcador, y entrega esos bloques estructurados al backend.


### Contrato visual exacto de En juego (v31)
Cada resultado finalizado se normaliza antes de llegar al frontend:
`ronda → ganadoras → perdedoras → marcador`.

Visualmente:
- nombre del torneo + ciudad + fechas;
- dónde verlo;
- tarjeta morada de Próximo partido con ronda, parejas y hora española;
- una única cabecera por ronda;
- debajo, cada partido con `Ganadoras 🏆`, perdedoras y marcador;
- marcador con `–` y desde la perspectiva de la pareja ganadora;
- estado `Finalizado` y fecha cuando FIP la proporciona.


### Estrategia v32 de En juego
Se elimina la dependencia del selector Female. Playwright captura todos los bloques de resultados visibles para cada día; después el backend filtra cada partido contra el ranking femenino dinámico. Solo se conserva un bloque si contiene exactamente cuatro jugadoras del ranking. A partir de ese bloque se determina la pareja ganadora, la perdedora y el marcador.


### v34 — En juego únicamente
Se restauran los dos normalizadores requeridos por `/api/live` y se añade validación de despliegue para evitar otro `NameError`. La captura recorre todos los estados de controles de género, organiza todos los bloques y filtra mujeres solo después.


### v35 — En juego únicamente
Corrección importante de refactorización: el parser de resultados se sustituye sin borrar los normalizadores usados por `/api/live`. Los partidos finalizados se localizan desde `✓`, se prueban todos los elementos clicables alrededor de `Female`, y el filtro femenino se aplica después usando el ranking.


### v36 — En juego únicamente
La pestaña recorre todos los días disponibles del torneo y acumula todos los partidos femeninos finalizados desde el inicio hasta hoy. Cada resultado conserva fecha, ronda, ganadoras, perdedoras y marcador; los partidos se deduplican antes de enviarse al frontend.


### v37 — rediseño visual de En juego
Solo se modifica la presentación de `En juego`: tarjeta principal con acento rosa/morado, bloque de próximo partido, rondas diferenciadas y marcadores resaltados en violeta. El backend v36 y las demás pestañas permanecen intactos.


### v38 — diseño exacto de En juego
Solo se modifica la presentación de `En juego` para acercarla literalmente al mockup compartido: misma jerarquía, misma paleta oscura con acentos violeta/rosa, tarjeta principal, bloque de próximo partido y tarjetas de resultados compactas.


### v39 — En juego: cuadro completo
Solo afecta a `En juego`. El extractor deja de depender de un único `✓`: activa el control Female de forma precisa, recorre el cuadro completo y todos los días disponibles, localiza contenedores mínimos con cuatro jugadoras y marcador, y acumula todos los partidos finalizados del torneo hasta la fecha actual.


### v43 — v39 exacta + diseño
El backend y el JavaScript de `En juego` son exactamente los de v39, la versión que mostró 21 resultados en Railway. Solo se ha modificado CSS para acercar la presentación al mockup de referencia.


### v44 — rondas y próximo partido sin tocar el extractor
`En juego` mantiene el extractor v39 exacto. La ronda se etiqueta después de obtener los resultados. El próximo partido usa Order of Play cuando está disponible y, si no, muestra como mínimo la fase actual y `horario por confirmar (hora de España)` sin inventar parejas.


### v46 — Live Score real de FIP
No se modifica el extractor v39 de resultados ni las rondas existentes. Se añade una consulta separada a la pestaña `Live Score` de FIP. Si detecta un partido femenino activo, el bloque superior muestra `🔴 EN DIRECTO`, las parejas y el marcador visible; cuando deja de estar en directo, vuelve a mostrarse el próximo partido del Order of Play.


### v47 — sin falsos indicadores de navegación
Solo cambia el diseño de `En juego`: se eliminan los símbolos `›` de las filas de resultados y del bloque superior porque no son enlaces ni abren ningún detalle. La extracción, rondas, Live Score y resto de la web permanecen intactos.


### v48 — Sponsors
Solo se modifica la pestaña Sponsors. Las tarjetas son más grandes y la foto de cada jugadora pasa a ser una imagen vertical destacada. La carga de foto prueba Wikipedia ES/EN por página exacta y por búsqueda; si no existe una foto pública disponible, se muestra un placeholder visual consistente en lugar de dejar el hueco vacío.


### v49 — Ranking y Torneos
El ranking pasa de Top 10 a Top 20, con tarjetas más grandes e imagen de cada jugadora (búsqueda automática en Wikipedia ES/EN y fallback visual). La pestaña Torneos pasa de mostrar 6 a mostrar hasta 10 torneos, manteniendo el torneo activo primero.


### v50 — Torneos actuales y futuros
La pestaña Torneos deja de mostrar eventos ya finalizados. Compara la fecha final de cada torneo con la fecha actual, mantiene el torneo activo primero y ordena los siguientes cronológicamente. Si la fuente externa falla o devuelve un calendario obsoleto, usa un fallback actualizado del calendario oficial Premier Padel 2026 desde agosto en adelante.

### v51 — corrección de fotos en Sponsors
Se corrige el render de imágenes para evitar composiciones partidas entre foto y fallback. La tarjeta usa una única imagen cuando carga correctamente y muestra el fallback completo solo cuando la foto falla. También se prioriza la página exacta de Wikipedia antes de hacer búsquedas genéricas.


### v52 — corrección de fotos en Ranking
Solo cambia la carga de imágenes del Ranking. Se evita mezclar foto y fallback, se priorizan páginas exactas de Wikipedia y se usa una única imagen completa por jugadora; si falla, se muestra el fallback completo.

### v53 — Sponsors rediseñados + fotos de Ranking
Sponsors y Ranking siguen siendo pestañas independientes. Sponsors adopta el diseño compacto aprobado: foto grande, ficha de jugadora y cuatro columnas de patrocinio sin espacio muerto. Ranking conserva Top 20 pero mejora el tamaño/crop de retratos y endurece la validación de Wikipedia para evitar asignar imágenes de personas incorrectas.


### v54 — fotos + fallback de En juego
Las imágenes de Ranking y Sponsors se sirven mediante el proxy `/api/photo` para evitar bloqueos de hotlink de Wikimedia. Se versiona la caché del navegador para descartar fallos antiguos. En `En juego`, el extractor v39 permanece como primera opción; únicamente si devuelve cero resultados se activa un segundo parser por proximidad DOM alrededor de `✓`.


### v55 — fotos resueltas en servidor
Ranking y Sponsors dejan de consultar Wikipedia desde JavaScript. El backend resuelve cada jugadora mediante Wikipedia ES/EN y Wikimedia Commons, valida nombre/apellido, cachea el resultado y sirve la imagen mediante `/api/photo`. Los logs muestran `photo: <jugadora> -> ...`, lo que permite diagnosticar cada imagen.


### v56 — alias de jugadoras para fotos
Se corrige la causa observada en logs: el ranking entrega nombres civiles completos mientras Wikipedia suele usar el nombre deportivo corto. El resolver genera alias como `Gemma Triay Pons → Gemma Triay`, `Marta Ortega Gallego → Marta Ortega`, etc., y prueba Wikipedia ES/EN y Commons. Además, el ranking deja de truncar nombres a tres palabras, evitando valores rotos como `Alejandra Alonso De`.

### v57 — fotos por nombre + primer apellido
La búsqueda de imágenes prioriza `nombre + primer apellido` y considera suficiente esa coincidencia. Ejemplo: `Gemma Triay Pons` se resuelve como `Gemma Triay`. El resto de la aplicación queda intacto.

### v58 — fotos desde perfiles oficiales FIP
Las fotos de jugadoras se buscan primero en el perfil oficial de FIP usando el nombre completo del ranking para construir el slug (`Delfina Brea Senesi → /player/delfina-brea-senesi/`). Wikipedia/Commons quedan como fallback. Esto evita depender de Google Images y reduce falsos positivos como una artista con el mismo nombre.


### v59 — fotos del Ranking desde PadelSpeak
El ranking intenta obtener la imagen directamente de la misma fila HTML de PadelSpeak que contiene posición, nombre, país y puntos. Si la fila incluye `img`, `data-src`, `data-lazy-src`, `srcset`, etc., esa imagen se usa primero. Solo si PadelSpeak no expone una foto se mantiene el resolver externo como fallback. El log de ranking ahora indica cuántas fotos se han encontrado (`fotos=N`).

### v60 — corrección del proxy de fotos FIP
Los perfiles FIP ya se estaban resolviendo correctamente, pero `/api/photo` enviaba siempre `Referer: commons.wikimedia.org`, incluso para imágenes de `padelfip.com`. Ahora el proxy detecta el dominio, usa el `Referer` correcto, valida que la respuesta sea realmente una imagen y añade logs `photo proxy ok/failed` para verificar el último paso.


### v61 — imagen directa por jugadora
Los logs demostraban que FIP resolvía correctamente los perfiles, pero no aparecía ninguna petición posterior a `/api/photo`. Para eliminar ese punto de fallo, Ranking y Sponsors ahora usan directamente `<img src="/api/player-photo-image?...">`. El nuevo endpoint resuelve la fuente y devuelve los bytes de imagen en una sola petición. Los logs indican `player-photo-image OK/FAILED`.

### v62 — En juego más grande
Solo cambia la escala visual de `En juego`: contenedor más ancho, cabecera de torneo más alta, textos más grandes, tarjeta de próximo partido más espaciosa y filas de resultados con mayor altura, tipografía y marcador. No se modifica ninguna lógica de resultados, Live Score, Ranking, Sponsors, Noticias o Torneos.


### v63 — combinación explícita v61 + v62
Incluye el sistema de fotos directas de v61 (`/api/player-photo-image`) y el rediseño ampliado de `En juego` de v62. No se cambia ninguna otra lógica.

### v64 — corrección real de petición de fotos
El backend ya resolvía los perfiles FIP, pero el frontend creaba cada `Image()` fuera del DOM y además marcaba `loading=lazy`. Eso podía impedir que el navegador llegara a solicitar `/api/player-photo-image`. Ahora la imagen se inserta primero en la tarjeta y después se asigna `src`, sin lazy loading. Ranking y Sponsors deben generar peticiones visibles en logs `GET /api/player-photo-image...`.

### v65 — Sponsors + En juego
Solo se cambia Sponsors y la escala visual de En juego. Sponsors usa el nombre completo del ranking para pedir la foto (por ejemplo `Gemma Triay → Gemma Triay Pons`), tiene un endpoint de imagen independiente y reduce el tamaño visual de la foto para evitar pixelación. En juego se amplía ligeramente una vez más sin tocar su lógica.


### v66 — parejas actuales en Sponsors + foto de Ari
Solo se modifica Sponsors. La pareja ya no sale del texto hardcodeado de `SPONSORS_DATA`: se deriva del ranking actual y se muestra únicamente el apellido de la compañera. Además, Ari Sánchez se empareja con `Ariana Sánchez...` del ranking por apellido + prefijo de nombre, por lo que su foto usa el mismo perfil FIP/estilo que las demás.


### v67 — Sponsors sin pareja
Se elimina únicamente la línea de pareja de las tarjetas de Sponsors. El resto permanece intacto.


### v68 — Sponsors más compactos y responsive
Se reduce el tamaño general de las tarjetas de Sponsors, incluida la foto. En móvil, la ficha pasa a una sola columna, la imagen baja de altura, el texto y ranking se reajustan y las marcas se apilan para evitar desbordamientos.


### v69 — En juego resistente a fallos intermitentes de FIP
Sponsors permanece exactamente como en v68. En `En juego`, si el extractor v39 devuelve cero bloques, se reintenta dos veces antes de usar el fallback alternativo. El último conjunto válido de resultados queda guardado en memoria durante 6 horas para que un fallo transitorio de FIP no vacíe la pestaña.


### v70 — partido actual / próximo partido dinámico
`En juego` solo muestra `EN DIRECTO` si FIP Live Score confirma explícitamente que el partido femenino está en curso. Cuando termina, el extractor de resultados lo recoge y pasa al bloque de resultados; entonces el bloque superior vuelve al próximo partido. Si Order of Play aún no publica parejas/hora, se mantiene el estado actual de `por confirmar`; en cuanto FIP publique esos datos, el valor se actualiza automáticamente. La caché de `/api/live` baja a 60 segundos para reflejar antes esos cambios.


### v71 — tarjetas oficiales del día FIP
`En juego` incorpora un parser nuevo para las tarjetas que FIP muestra en el Order of Play del día. Lee directamente `WOMEN`, la ronda (`SEMIFINALS`, etc.), las jugadoras, los marcadores y el estado (`COMPLETED`, partido con score en curso o próximo). Un `COMPLETED` se añade inmediatamente a Resultados; un partido en curso ocupa la tarjeta superior como `EN DIRECTO`; si no hay directo se usa la siguiente tarjeta pendiente como `Próximo partido`. Los parsers v39 y Live Score anteriores permanecen como respaldo.


### v72 — responsive móvil global
Se añade una capa responsive para pantallas pequeñas sin alterar el diseño desktop. En móvil, textos largos hacen wrap, las tarjetas nunca exceden el ancho de pantalla, `En juego` reorganiza próximo partido y resultados a una sola columna, Ranking/Sponsors/Torneos se compactan y el menú superior pasa a scroll horizontal en vez de cortarse.
