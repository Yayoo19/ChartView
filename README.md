# ChartView

Aplicación de escritorio en Python para graficar acciones de México (BMV), EE.UU. y criptomonedas, con más de 70 indicadores técnicos. Corre localmente; los datos se descargan de Yahoo Finance.

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

## Interfaz

- **Barra superior:** buscador, intervalos rápidos (5m, 15m, 1h, 4h, D, S, M) y la lista con todos los intervalos; luego el tipo de gráfica, Indicadores, Alertas y ⟳ (actualizar).
- **Debajo de la gráfica:** cuánta historia ver (1D, 5D, 1M, 3M, 6M, YTD, 1A, 2A, 5A, 10A, Todo), escala Log, actualización Auto, Ver todo y Recientes. En intervalos intradía se desactivan los rangos que Yahoo no ofrece.
- **Menú:** Archivo (buscar, importar CSV, guardar imagen), Ver (listas, escala, actualización, pantalla completa), Herramientas (indicadores, alertas, clave de FRED) y Ayuda (atajos de teclado: F1).

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
- Tipos de gráfica: velas, Heikin Ashi, barras OHLC, línea y área.
- **Log** (abajo de la gráfica o Alt+L): escala logarítmica en la gráfica de precio, incluidos los indicadores dibujados sobre ella.
- Intervalos de 1 minuto a 12 meses. Las velas de 4 horas se arman con datos de 1 hora, y las de 2 semanas con velas semanales (siempre empiezan en lunes). Las de 3, 6 y 12 meses se arman con velas mensuales, alineadas a trimestres, semestres y años del calendario.
- El panel de listas se puede cerrar con la ✕ o arrastrar a otro lado. Se vuelve a abrir con **☰ Listas** (primer botón de la barra) o Ctrl+B. La app recuerda dónde lo dejaste.
- Varias listas de seguimiento: el menú ⋯ crea, duplica, renombra y elimina listas. Cada símbolo muestra debajo el nombre de la empresa o serie. Con clic derecho copias un símbolo a otra lista o creas una alerta.
- **Dibujo** (barra vertical a la izquierda de la gráfica; se oculta y se muestra con **✎ Dibujo** en la barra superior, Ver → Herramientas de dibujo o Ctrl+D):
  - Herramientas agrupadas: cursor; líneas (horizontal, vertical, tendencia, rayo); formas (rectángulo, círculo/elipse, dibujo libre); texto e imagen (PNG/JPG…); Fibonacci manual; borrador. La flechita de cada grupo abre sus herramientas y el botón recuerda la última que usaste.
  - Con el cursor (↖) puedes mover y redimensionar lo dibujado. Clic derecho sobre un dibujo cambia el color o el grosor, o lo elimina.
  - Esc cancela la herramienta, Ctrl+Z deshace, 👁 oculta los dibujos y 🗑 los borra todos.
  - Se guardan por símbolo, anclados a fecha y precio, así que siguen en su lugar al cambiar el intervalo, el periodo o la escala log.
  - Las imágenes se copian a `%USERPROFILE%\.chartview\images`.
- **Alertas** (🔔 en la barra o Ctrl+Alt+A):
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
