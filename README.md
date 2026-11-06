# ChartView

Aplicación de escritorio en Python para graficar acciones de México (BMV), EE.UU. y criptomonedas, con más de 70 indicadores técnicos. Corre localmente; los datos se descargan de Yahoo Finance y, para cripto, de Binance.

## Ejecutar

Requiere Python 3.10 o más reciente. Desde la carpeta del proyecto, la primera vez:

```
python -m venv .venv
```

Windows:

```
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python src\main.py
```

Linux / macOS:

```
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python src/main.py
```

Las siguientes veces basta con la última línea.

## Símbolos

Hay un solo buscador (arriba a la izquierda, Ctrl+L) para todos los mercados. Escribe un símbolo o un nombre ("walmex", "apple", "bitcoin", "nikkei", "bono 10") y elige una sugerencia. Si presionas Enter sin elegir, se abre la mejor sugerencia. Si escribes un nombre que no es un símbolo, se busca por nombre.

| Región | Ejemplos |
|---|---|
| México (BMV) | `WALMEX.MX`, `AMXB.MX`, `GFNORTEO.MX` · IPC: `^MXX` · dólar: `MXN=X` |
| EE.UU. | `AAPL`, `SPY`, `^GSPC`, `GC=F` |
| Asia | `^N225` (Nikkei), `^HSI` (Hang Seng), `^KS11` (KOSPI), `000001.SS` (Shanghai) · acciones: `7203.T` Tokio, `0700.HK` Hong Kong, `600519.SS` China, `005930.KS` Corea |
| Macro EE.UU. | `MACRO:M2`, `MACRO:FED_BALANCE`, `MACRO:UST2Y`, `MACRO:UST10Y`, `MACRO:SPREAD_10Y2Y` (Reserva Federal vía DBnomics) · bonos en Yahoo: `2YY=F`, `^TNX`, `ZT=F`, `ZN=F`, `TLT` · con clave de FRED: `FRED:WALCL`, `FRED:M2SL`… |
| Cripto | `BTC-USD`, `ETH-USD`, `SOL-USD`, `BTC-EUR` |

**Archivo → Búsqueda avanzada** (Ctrl+F) muestra los resultados en una tabla con bolsa y tipo.

### Comparar, ratios y diferenciales

- **Comparar** (botón ⇄ Comparar): agrega uno o más símbolos sobre la gráfica. El eje pasa a **% de cambio desde el inicio del periodo** (1A, 5A…, abajo de la gráfica), así todas las líneas arrancan en 0 %. La leyenda muestra el % y el precio de cada uno, y con ✕ (o clic derecho) quitas una comparación.
- **Ratio:** escribe `A/B` en el buscador, por ejemplo `AAPL/^GSPC` (fuerza relativa contra el S&P 500), `GC=F/SI=F` (oro/plata) o `BTC-USD/ETH-USD`.
- **Diferencial:** escribe `A - B` con espacios alrededor del guion, por ejemplo `MACRO:UST10Y - MACRO:UST2Y`. Los espacios hacen falta porque hay tickers con guion, como `BTC-USD`.
- Los ratios y diferenciales se comportan como cualquier símbolo: velas, indicadores, dibujos, alertas y lista de seguimiento. Si los mercados tienen horarios distintos (cripto contra acciones), se usa el último dato disponible de cada uno.

## Interfaz

- **Barra superior:** menú ☰, ✎ (herramientas de dibujo), buscador, intervalos rápidos (5m, 15m, 1h, 4h, D, S, M) y la lista con todos los intervalos; luego el tipo de gráfica, Indicadores, Comparar, el diseño de gráficas y ⟳ (actualizar).
- **Encabezado:** el símbolo, el precio y los datos de la vela van dentro de la gráfica, arriba a la izquierda, y dejan pasar el mouse.
- **Columna de iconos a la derecha:** 📋 listas de seguimiento, 📊 análisis y 🔔 alertas, con el número de alertas activas. Las listas y el análisis abren un panel a la vez; otro clic lo cierra. La app recuerda cuál dejaste abierto.
- **Panel inferior:** un clic en una pestaña lo abre, otro clic en la misma lo pliega (también Ctrl+J), y su borde superior cambia la altura.
  - **Movimientos:** los símbolos de la lista activa, de mayor a menor cambio. Un clic abre el símbolo.
  - **Alertas:** las últimas alertas disparadas. Un clic abre el símbolo.
  - **Notas:** texto libre por símbolo, que se guarda solo.
- **Debajo de la gráfica:** cuánta historia ver (1D, 5D, 1M, 3M, 6M, YTD, 1A, 2A, 5A, 10A, Todo), escala Log, actualización Auto, Ver todo y Recientes. En intervalos intradía se desactivan los rangos que Yahoo no ofrece.
- **Panel de análisis** (📊 a la derecha, Ver → Panel de análisis o Ctrl+E), para acciones con cobertura de analistas:
  - Próximo reporte trimestral con su fecha, la utilidad por acción y los ingresos estimados.
  - Historial de reportes: estimado contra real y sorpresa en %.
  - Estimaciones del trimestre y año actual y siguiente.
  - Recomendaciones de analistas (consenso y su cambio en 3 meses) y precio objetivo a 12 meses (bajo, promedio, alto) con el potencial contra el precio actual.
  - Últimos cambios de calificación (EE.UU.) y la próxima fecha de dividendo.
  - Datos de Yahoo Finance. Cripto, índices, materias primas y series macro no tienen esta información.
- **Paneles inferiores (RSI, MACD, volumen…):** arrastra el borde entre dos paneles para cambiar su altura; la app la recuerda. Arrastra el eje de valores de un panel o usa la rueda sobre él para estirar la escala, y después arrastra dentro del panel para moverla en vertical. Doble clic en el eje vuelve a la escala automática, doble clic en un borde a las alturas originales, y Ver → Restablecer paneles inferiores deshace todo. La escala a mano se mantiene al actualizar y se reinicia al cambiar de símbolo o intervalo.
- **Cuenta regresiva:** en todos los intervalos, la etiqueta del último precio muestra cuánto falta para que cierre la vela actual, según el horario de cada mercado (cripto opera todo el día y cierra a la medianoche UTC). Las velas de semana, mes o más cierran con la última sesión hábil de su periodo. Si la vela ya cerró (mercado cerrado) no se muestra.
- **Cuadrícula de fondo:** Ver → Cuadrícula (sin cuadrícula, suave, media o fuerte); Ctrl+G la enciende y apaga.
- **Menú ☰:** Archivo (buscar, importar CSV, guardar imagen), Ver (paneles, escala, cuadrícula, actualización, pantalla completa), Herramientas (indicadores, alertas, clave de FRED) y Ayuda (atajos de teclado: F1).

### Varias gráficas

- **Diseño:** el botón de cuadrícula arriba a la derecha (o Ver → Diseño de gráficas) acomoda de 1 a 8 gráficas en columnas, filas o cuadrícula. Arrastra el borde entre dos gráficas para cambiar su tamaño. La app recuerda el diseño, los tamaños y cada gráfica.
- **Gráfica activa:** la del borde azul; se elige con un clic. La barra superior, los rangos, Indicadores, Comparar, el panel de análisis y la lista de seguimiento actúan sobre ella. El símbolo, el intervalo, el periodo, el tipo, la escala log, los indicadores y las comparaciones son propios de cada gráfica.
- **Al agregar gráficas:** una nueva empieza como copia de la activa. Si reduces el diseño, las que sobran se guardan y regresan al volver a ampliarlo.
- **Sincronizar** (en el mismo botón): símbolo, intervalo, cursor (encendido por omisión) y rango de tiempo. Sincronizar el rango también lleva a todas el periodo que elijas abajo de la gráfica. Un símbolo solo pasa a las demás cuando ya cargó, así que uno mal escrito no las afecta.
- **Cursor y rango entre mercados:** en intradía se alinean por el instante real, aunque cada gráfica muestre la hora de su mercado: las 15:30 de Nueva York coinciden con las 19:30 o 20:30 UTC de Binance, según el horario de verano. Si alguna de las gráficas es diaria o mayor, se alinean por la fecha de calendario. Las series sin zona horaria conocida, como las macro o los CSV, se alinean por la fecha que muestran.
- **Dibujos:** son del símbolo, así que aparecen en todas las gráficas que lo muestren. La barra de dibujo es una sola: elige la herramienta y dibuja en la gráfica que quieras.
- **Actualización:** ⟳ y Auto actualizan todas las gráficas; las que muestran lo mismo comparten una sola descarga. Con muchas gráficas distintas y Auto muy frecuente, Yahoo podría limitar las peticiones.

### Datos macro

- **Catálogo de la Fed** (sin registro): M2 y M1 (H.6, mensual desde 1959), balance de la Fed (H.4.1, semanal desde 2002), tasas del Tesoro y fondos federales (H.15, diario) y los diferenciales de la curva.
- **FRED**: en Herramientas → *Clave API de FRED* escribes una clave gratuita (https://fredaccount.stlouisfed.org/apikeys). Con ella abres cualquier serie de FRED escribiendo `FRED:` y su ID, por ejemplo `FRED:WALCL`.
- **Series propias / ISM PMI**: el ISM PMI no se publica en ninguna fuente gratuita (FRED lo retiró en 2016). Guarda el histórico en un CSV con dos columnas, fecha y valor, e impórtalo en Archivo → *Importar serie desde CSV*. Queda como `CSV:<NOMBRE>`. Para actualizarlo, vuelve a importar el CSV con el mismo nombre.
- Las series con un solo valor por fecha se dibujan como línea. Con un intervalo más amplio que la serie (p. ej. tasas diarias en 1 semana) se arman velas.
- Para ver el crecimiento anual de M2, agrega el indicador ROC con periodo 12 en intervalo mensual.

## Uso

- Rueda del mouse: zoom en el tiempo · arrastrar: desplazar · el eje Y se ajusta solo.
- **Indicadores** (Ctrl+I): catálogo por categorías, parámetros editables, color y orden. La flecha del botón agrega un indicador directo con valores por defecto.
- **Leyenda de la gráfica:** al pasar el mouse sobre un indicador (por ejemplo "EMA 20") aparecen ⚙ configurar, 👁 ocultar/mostrar y ✕ quitar; con clic derecho, el mismo menú. Los ocultos quedan en gris para volver a mostrarlos.
- Tipos de gráfica: velas, velas huecas, Heikin Ashi, barras OHLC, línea, área, línea base, Renko, Kagi, Point & Figure y Line Break.
  - **Línea base:** verde arriba y rojo abajo de la mitad del rango visible.
  - **Renko, Kagi, Point & Figure y Line Break** no siguen el tiempo: los indicadores y dibujos se calculan sobre sus barras, la etiqueta de precio muestra el último precio real y no hay cuenta regresiva. Comparar no aplica en estos tipos. El botón ⚙ junto al tipo ajusta el tamaño del ladrillo o cuadro, la reversa o las líneas; en 0, el tamaño sale del ATR de 14 velas.
- **%** (abajo de la gráfica o Alt+P): etiqueta el eje en % desde la primera vela visible. Se excluye con Log.
- **Invertir** (abajo de la gráfica, Ver → Invertir escala o Alt+I): voltea la gráfica de precio de cabeza, con los precios altos abajo, para ver el movimiento al revés y revisar si un patrón se sostiene sin el sesgo alcista o bajista. Se guarda por gráfica y se combina con Log o %. Los indicadores de los paneles inferiores no se voltean.
- **Log** (abajo de la gráfica o Alt+L): escala logarítmica en la gráfica de precio, incluidos los indicadores dibujados sobre ella.
- Intervalos de 1 minuto a 12 meses. Las velas de 4 horas se arman con datos de 1 hora, las de 2 y 3 días con velas diarias, y las de 2 semanas con velas semanales (siempre empiezan en lunes). Las de 2 y 3 días cuentan días hábiles, salvo en cripto, que cuenta días de calendario; un festivo acorta la vela que le toca. Las de 3 días de BTC y ETH coinciden con las de Binance; en otros pares los bloques pueden empezar en otro día, porque Binance los cuenta desde que listó cada par. Las de 2, 3, 6 y 12 meses se arman con velas mensuales, alineadas a bimestres, trimestres, semestres y años del calendario.
- El panel de listas se abre y se cierra con 📋 a la derecha, con la ✕ o con Ctrl+B.
- Varias listas de seguimiento: el menú ⋯ crea, duplica, renombra y elimina listas. Cada símbolo muestra debajo el nombre de la empresa o serie. Con clic derecho copias un símbolo a otra lista o creas una alerta.
- Orden y secciones a tu gusto:
  - **Ordenar:** arrastra un símbolo para cambiarlo de lugar o pasarlo a otra sección. También puedes arrastrar una sección completa.
  - **Secciones:** crea secciones con el nombre que quieras desde el menú ⋯ o con clic derecho. Con clic derecho sobre una sección la renombras, la ordenas de la A a la Z o la eliminas.
  - **Mover sin arrastrar:** clic derecho sobre el símbolo → «Mover a sección».
  - **Agregar actual:** pone el símbolo en la sección seleccionada. Si no hay ninguna seleccionada, va a la de su mercado. El orden nunca cambia solo.
- **Dibujo** (barra vertical a la izquierda de la gráfica; se oculta y se muestra con **✎** en la barra superior, Ver → Herramientas de dibujo o Ctrl+D):
  - Herramientas agrupadas: cursor; líneas (horizontal, rayo horizontal, vertical, tendencia, rayo, flecha); canales (canal paralelo, tridente de Andrews); formas (rectángulo, círculo/elipse, dibujo libre); texto e imagen (PNG/JPG…); Fibonacci (retroceso y extensión por tendencia); trading (medir, posición larga, posición corta, VWAP anclado); borrador. La flechita de cada grupo abre sus herramientas y el botón recuerda la última que usaste.
  - **Medir:** Shift+clic (o Shift+arrastrar) en cualquier momento, o la herramienta 📏. Muestra el cambio de precio, el %, las velas y el tiempo. Otro clic o Esc la quita; no se guarda.
  - **Posición larga o corta:** clic o arrastra hasta el objetivo. Las asas ajustan objetivo, stop y ancho, y la caja muestra el riesgo/beneficio.
  - **VWAP anclado:** clic en la vela de inicio. Arrastra el asa para cambiarla. Las series sin volumen no lo tienen.
  - **Canal, tridente y extensión de Fibonacci** se trazan con 3 clics.
  - **Sobre indicadores:** todas las herramientas funcionan también en los paneles inferiores (RSI, MACD, volumen…) para marcar tendencias o formaciones: dibuja en el panel que quieras. Cada dibujo queda ligado a ese indicador y a su valor, y reaparece si lo quitas y lo vuelves a agregar. Las líneas verticales cruzan siempre todos los paneles.
  - **Imán (🧲):** al dibujar, el punto se pega a la apertura, máximo, mínimo o cierre de la vela. En un panel se pega a las líneas del indicador. En modo débil solo si estás cerca; en modo fuerte, siempre. Un clic en 🧲 lo enciende o apaga y la flechita elige el modo. No aplica al dibujo libre.
  - Con el cursor (↖) puedes mover y redimensionar lo dibujado. Clic derecho sobre un dibujo cambia el color o el grosor, lo duplica, lo bloquea o lo elimina. Uno bloqueado no se mueve ni se borra con el borrador.
  - Esc cancela la herramienta, Ctrl+Z deshace y Ctrl+Y rehace (crear, mover, cambiar de estilo y borrar, por símbolo y mientras la app siga abierta), 👁 oculta los dibujos y 🗑 los borra todos.
  - Se guardan por símbolo, anclados a fecha y precio, así que siguen en su lugar al cambiar el intervalo, el periodo o la escala log.
  - Las imágenes se copian a `%USERPROFILE%\.chartview\images`.
- **Alertas** (🔔 a la derecha o Ctrl+Alt+A):
  - Condiciones: el precio cruza un nivel o queda por encima/debajo de él; el cambio del día sube o cae más de X %; el RSI cruza un umbral; el precio cruza una SMA/EMA en el intervalo que elijas.
  - Funcionan con acciones, índices, cripto y series macro.
  - Avisan con una notificación de escritorio, un sonido y parpadeo en la barra de tareas. Al hacer clic en la notificación se abre la gráfica.
  - Se revisan cada 30 s a 15 min. "Una vez" desactiva la alerta al dispararse; "Cada vez" la vuelve a armar cuando la condición deja de cumplirse.
  - Las de cruce solo avisan cuando el cruce ocurre. Las de nivel avisan también si ya se cumplen al crearlas.
  - Las alertas de precio del símbolo abierto se ven como líneas naranjas 🔔. Con clic derecho sobre una línea horizontal dibujada eliges "Crear alerta en este precio".
  - Opcional: al cerrar la ventana, ChartView sigue vigilando desde la bandeja del sistema. Se cierra del todo con clic derecho en su icono → Salir.
  - Las alertas solo se revisan mientras ChartView está abierto (o en la bandeja).
- Actualización automática, lista de seguimiento con precios y captura PNG (Ctrl+S).
- Todos los atajos de teclado están en Ayuda → Atajos de teclado (F1).

La configuración se guarda en `%USERPROFILE%\.chartview\config.json`.

## Indicadores

- **Medias móviles:** SMA, EMA, WMA, HMA, DEMA, TEMA, VWMA, KAMA, ALMA, LSMA, McGinley, cinta de EMAs.
- **Bandas y canales:** Bollinger, Keltner, Donchian, Envolventes, Ichimoku, Pivotes.
- **Tendencia:** Parabolic SAR, Supertrend, Chandelier Exit, ZigZag, Fractales, ADX/DMI, Aroon, Oscilador Aroon, Vortex.
- **Osciladores:** RSI, MACD, Estocástico, StochRSI, CCI, Williams %R, ROC, Momentum, TRIX, Awesome, Ultimate, PPO, TSI, Elder Ray, DPO, Coppock, Fisher, KST, CMO, RVGI, BOP, Squeeze Momentum, Schaff Trend Cycle, Connors RSI.
- **Volumen:** Volumen, VWAP, OBV, MFI, CMF, A/D, Chaikin Oscillator, Force Index, EOM, PVT, Klinger.
- **Fibonacci y Gann:** Retroceso de Fibonacci automático (con extensiones), Zonas de tiempo de Fibonacci, Bandas de Fibonacci, Abanico de Gann, Cuadrado de 9 de Gann, Gann HiLo Activator. Se calculan sobre el máximo y el mínimo de las últimas N velas (parámetro «Velas a analizar»).
- **Volatilidad:** ATR, NATR, Desviación estándar, Volatilidad histórica, %B, Ancho de Bollinger, Choppiness, Mass Index, Ulcer Index.

Para agregar uno nuevo, decora una función en `src/indicators.py` con `@indicator(...)`. Aparece solo en los menús.

## Notas

- Yahoo limita la historia intradía: 1 min ≈ 7 días, 2–30 min ≈ 60 días, 1 hora ≈ 2 años. La app ajusta el periodo automáticamente.
- Los datos de la BMV en Yahoo pueden tener un retraso de unos 15–20 minutos.
- La cripto se descarga de Binance, sin registro; el encabezado de la gráfica indica la fuente. Los símbolos `-USD` usan el par en USDT, así que el precio y el volumen no coinciden exactamente con los de Yahoo. Se usa Yahoo en el intervalo de 2 minutos, en los periodos anteriores a que Binance listara el par (BTC: 2017), en «Máximo» y en las monedas que Binance no tiene o retiró; también si Binance no responde.
