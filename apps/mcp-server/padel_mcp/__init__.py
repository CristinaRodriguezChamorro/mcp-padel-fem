"""
padel_mcp — el MCP de verdad: las herramientas que cualquier modelo puede llamar.

Aquí vive TODO lo que habla con un modelo y nada de la web:
  tools.py    las herramientas (qué parámetros aceptan y qué devuelven) y la puerta de acceso
  auth.py     quién puede llamar (token de GitHub, límites)

Por defecto expone tres herramientas: ranking_femenino, ultimo_torneo y proximo_torneo. Las demás
(jugadora, noticias, calendario...) se activan con MCP_TOOLS=extended.

No sabe nada de HTML ni de FastAPI. Lee los datos de la capa compartida (carpeta tools/: Wikipedia,
RSS...), la misma que usa la web. La web (server.py) solo monta este paquete en /ai/mcp.
"""
