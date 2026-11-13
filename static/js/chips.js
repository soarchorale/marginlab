// ── State ──────────────────────────────────────────────────────
// TOTAL_RECORDS is injected by the HTML template into a global variable.
var state = {
  frameIdx:   0,
  totalFrames: Number(document.getElementById('app').dataset.total) || 1000,
  playing:    false,
  timer:      null,
  scheme:     1,
  cfg: {
    window_size:    200,
    bins:           40,
    time_layers:    10,
    hist_price_min: null,
    hist_price_max: null,
    ma_window:      20,
    price_view_min: null,
    price_view_max: null,
    x_view_size:    240,
    interval:       200,
    step:           3,
  }
};

// ── ECharts instances ─────────────────────────────────────────────
var frameRequestId = 0;
var priceChartInst = null;
var volChartInst   = null;

function ensureCharts() {
  if (!priceChartInst) {
    var priceEl = document.getElementById('priceChart');
    var volEl   = document.getElementById('volChart');

    priceChartInst = echarts.init(priceEl);
    volChartInst   = echarts.init(volEl);

    [priceChartInst, volChartInst].forEach(function(c) {
      c.getDom().style.background = '#ffffff';
    });
  }
}

// ── Utilities ──────────────────────────────────────────────────
function timeToColor(t, isBlue) {
  return isBlue ? 'rgba(25,140,128,' + (0.25+t*0.65) + ')' : 'rgba(212,142,80,' + (0.25+t*0.65) + ')';
}

function schemeDesc(s) {
  return ['', '左右对称：买入→右 / 卖出→左',
              '同侧堆叠：橙=卖出  绿=买入',
              '净买入量：正→右(买压) 负→左(卖压)'][s];
}

function normalizeViewSize(v) {
  var size = parseInt(v, 10);
  if (isNaN(size)) size = 240;
  return Math.max(20, size);
}

function buildXView(ps, frameIdx) {
  var viewSize = normalizeViewSize(state.cfg.x_view_size);
  var currentIdx = Math.max(0, parseInt(frameIdx, 10) || 0);
  var xMin = Math.floor(currentIdx / viewSize) * viewSize;
  var xMax = xMin + viewSize - 1;
  var start = Math.max(0, xMin);
  var end = Math.min(ps.timestamps.length - 1, xMax);
  var labelByIndex = {};

  for (var i = start; i <= end; i++) {
    labelByIndex[i] = ps.timestamps[i];
  }

  return {
    min: xMin,
    max: xMax,
    start: start,
    end: end,
    viewSize: viewSize,
    tickInterval: Math.max(1, Math.ceil(viewSize / 6)),
    labelByIndex: labelByIndex
  };
}

function timeAxisLabel(value, labelByIndex) {
  var idx = Math.round(Number(value));
  var label = labelByIndex[idx];
  if (!label) return '';
  return label.slice(5, 10) + '\n' + label.slice(11, 16);
}

function tooltipFormatter(params, labelByIndex) {
  if (!params || !params.length) return '';
  var idx = Math.round(Number(params[0].axisValue));
  var title = labelByIndex[idx] || ('#' + idx);
  var rows = [title];

  params.forEach(function(p) {
    if (p.seriesName === '筹码分布') return;
    var value = Array.isArray(p.value) ? p.value[1] : p.value;
    if (value == null || value === '-') return;
    rows.push(p.marker + p.seriesName + ': ' + value);
  });

  return rows.join('<br/>');
}

function buildVisibleSeries(ps, xView) {
  var prices = [], ma = [], buyPts = [], sellPts = [];
  var buyVols = [], sellVols = [];

  for (var i = xView.start; i <= xView.end; i++) {
    var price = ps.prices[i];
    var volume = ps.volumes[i] || 0;
    var isBuy = ps.directions[i] === 1;

    prices.push([i, price]);
    ma.push([i, ps.ma[i]]);
    buyVols.push([i, isBuy ? volume : 0]);
    sellVols.push([i, isBuy ? 0 : volume]);

    if (isBuy) {
      buyPts.push([i, price]);
    } else {
      sellPts.push([i, price]);
    }
  }

  return {
    prices: prices,
    ma: ma,
    buyPts: buyPts,
    sellPts: sellPts,
    buyVols: buyVols,
    sellVols: sellVols
  };
}

// ── Render the combined price chart and volume profile ─────────────────────────────────
function renderPrice(ps, chipData, latestPrice, scheme, yMin, yMax, xView) {
  if (!priceChartInst) return;
  var visible = buildVisibleSeries(ps, xView);

  // ── Build chip bar data for custom series ──
  // Compute the max volume extent to scale the bars within the right portion
  var chipBarGroups = [];  // array of { priceCenter, binHalf, segments[] }
  var binHalf = (chipData[0].price_high - chipData[0].price_low) / 2;
  var maxAbsVol = 0;

  chipData.forEach(function(d) {
    var segments = [];
    if (scheme === 1) {
      // Symmetric: buy goes right (positive), sell goes left (negative)
      var buyAcc = 0, sellAcc = 0;
      d.layers.forEach(function(layer) {
        segments.push({ start: buyAcc, end: buyAcc + layer.buy_vol, mid_t: layer.time_mid, isBuy: true });
        buyAcc += layer.buy_vol;
        segments.push({ start: -sellAcc, end: -(sellAcc + layer.sell_vol), mid_t: layer.time_mid, isBuy: false });
        sellAcc += layer.sell_vol;
      });
      maxAbsVol = Math.max(maxAbsVol, buyAcc, sellAcc);
    } else if (scheme === 2) {
      // Stacked: sell first then buy, all positive
      var acc = 0;
      d.layers.forEach(function(layer) {
        segments.push({ start: acc, end: acc + layer.sell_vol, mid_t: layer.time_mid, isBuy: false });
        acc += layer.sell_vol;
      });
      d.layers.forEach(function(layer) {
        segments.push({ start: acc, end: acc + layer.buy_vol, mid_t: layer.time_mid, isBuy: true });
        acc += layer.buy_vol;
      });
      maxAbsVol = Math.max(maxAbsVol, acc);
    } else {
      // Net: positive = buy pressure, negative = sell pressure
      var posAcc = 0, negAcc = 0;
      d.layers.forEach(function(layer) {
        var nv = layer.net_vol;
        if (nv >= 0) {
          segments.push({ start: posAcc, end: posAcc + nv, mid_t: layer.time_mid, isBuy: true });
          posAcc += nv;
        } else {
          segments.push({ start: negAcc, end: negAcc + nv, mid_t: layer.time_mid, isBuy: false });
          negAcc += nv;
        }
      });
      maxAbsVol = Math.max(maxAbsVol, posAcc, Math.abs(negAcc));
    }
    chipBarGroups.push({ priceCenter: d.price_center, binHalf: binHalf, segments: segments });
  });

  // The chip bars occupy the right 25% of the grid width
  var chipWidthRatio = 0.25;

  // Flatten all segments into a single data array for the custom series
  var chipSeriesData = [];
  chipBarGroups.forEach(function(group) {
    group.segments.forEach(function(seg) {
      chipSeriesData.push({
        priceCenter: group.priceCenter,
        binHalf: group.binHalf,
        start: seg.start,
        end: seg.end,
        mid_t: seg.mid_t,
        isBuy: seg.isBuy,
        maxVol: maxAbsVol
      });
    });
  });

  var chipCustomSeries = {
    name: '筹码分布',
    type: 'custom',
    renderItem: function(params, api) {
      var d = chipSeriesData[params.dataIndex];
      if (!d) return;

      var coordSys = params.coordSys;
      var gridRect = coordSys ? coordSys.width : api.getWidth() - 55 - 20;
      var chipPixelWidth = gridRect * chipWidthRatio;
      var gridRight = coordSys ? coordSys.x + coordSys.width : api.getWidth() - 20;

      // Y coordinates from price axis
      var yTop = api.coord([xView.max, d.priceCenter + d.binHalf])[1];
      var yBot = api.coord([xView.max, d.priceCenter - d.binHalf])[1];

      // Scale volume to pixel offset within chip area
      var scale = d.maxVol > 0 ? chipPixelWidth / d.maxVol : 0;
      var x0, x1;
      if (scheme === 1 || scheme === 3) {
        // Center baseline at gridRight, positive goes left, negative goes right
        var baseline = gridRight - chipPixelWidth / 2;
        scale *= 0.5;
        x0 = baseline - d.start * scale;
        x1 = baseline - d.end * scale;
      } else {
        // Scheme 2: all bars grow leftward from right edge
        x0 = gridRight - d.start * scale;
        x1 = gridRight - d.end * scale;
      }

      // Ensure x0 < x1
      if (x0 > x1) { var tmp = x0; x0 = x1; x1 = tmp; }

      return {
        type: 'rect',
        shape: { x: x0, y: Math.min(yTop, yBot), width: x1 - x0, height: Math.abs(yBot - yTop) },
        style: { fill: timeToColor(d.mid_t, d.isBuy) },
        silent: true,
        z2: 1,
      };
    },
    data: chipSeriesData.map(function(d, i) { return [xView.max, d.priceCenter]; }),
    z: 1,
    silent: true,
  };

  // Current price mark line on the price line series
  var priceLineSeries = {
    name: '价格', type: 'line',
    data: visible.prices,
    lineStyle: { color: '#7a8a93', width: 1 },
    symbol: 'none',
    areaStyle: {
      color: { type: 'linear', x:0, y:0, x2:0, y2:1,
        colorStops: [
          { offset: 0, color: 'rgba(25,140,128,0.09)' },
          { offset: 1, color: 'rgba(25,140,128,0)' }
        ]
      }
    },
    z: 5,
    markLine: {
      silent: true,
      symbol: ['none', 'none'],
      lineStyle: { color: '#c58c42', width: 1.5, type: 'dashed' },
      label: {
        formatter: '当前价: ' + latestPrice.toFixed(2),
        color: '#c58c42', fontSize: 11
      },
      data: [{ yAxis: latestPrice }]
    }
  };

  var schemeLabel = scheme === 1 ? '对称' : scheme === 2 ? '堆叠' : '净值';

  priceChartInst.setOption({
    backgroundColor: '#ffffff',
    animation: false,
    grid: { left: 55, right: 20, top: 40, bottom: 40 },
    title: {
      text: '价格走势 + 筹码分布(' + schemeLabel + ')',
      textStyle: { color: '#198c80', fontSize: 13 },
      left: 'center', top: 4
    },
    xAxis: {
      type: 'value',
      min: xView.min,
      max: xView.max,
      interval: xView.tickInterval,
      minInterval: 1,
      axisLabel: { show: false },
      splitLine: { show: false },
      axisLine: { lineStyle: { color: '#dce4e7' } },
    },
    yAxis: {
      type: 'value', min: yMin, max: yMax,
      axisLabel: { color: '#7a8a93', fontSize: 10 },
      splitLine: { lineStyle: { color: '#edf0f2', type: 'dashed' } },
    },
    series: [
      chipCustomSeries,
      priceLineSeries,
      {
        name: 'MA' + ps.ma_window, type: 'line',
        data: visible.ma,
        lineStyle: { color: '#c58c42', width: 1.5 },
        symbol: 'none', connectNulls: false, z: 6,
      },
      {
        name: '买入', type: 'scatter',
        data: visible.buyPts,
        symbol: 'triangle', symbolSize: 7,
        itemStyle: { color: '#198c80', opacity: 0.7 },
        z: 7,
      },
      {
        name: '卖出', type: 'scatter',
        data: visible.sellPts,
        symbol: 'triangle', symbolSize: 7,
        symbolRotate: 180,
        itemStyle: { color: '#d48e50', opacity: 0.7 },
        z: 7,
      },
    ],
    legend: {
      data: ['价格', 'MA' + ps.ma_window, '买入', '卖出'],
      textStyle: { color: '#7a8a93', fontSize: 10 },
      top: 6, right: 10,
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#edf0f2',
      borderColor: '#dce4e7',
      textStyle: { color: '#32444e', fontSize: 11 },
      formatter: function(params) {
        return tooltipFormatter(params, xView.labelByIndex);
      },
    },
  }, true);
}


// ── Render the volume chart ──────────────────────────────────────────────
function renderVol(ps, xView) {
  if (!volChartInst) return;
  var visible = buildVisibleSeries(ps, xView);

  volChartInst.setOption({
    backgroundColor: '#ffffff',
    animation: false,
    grid: { left: 55, right: 20, top: 24, bottom: 40 },
    title: {
      text: '成交量',
      textStyle: { color: '#198c80', fontSize: 12 },
      left: 'center', top: 2
    },
    xAxis: {
      type: 'value',
      min: xView.min,
      max: xView.max,
      interval: xView.tickInterval,
      minInterval: 1,
      axisLabel: {
        color: '#7a8a93', fontSize: 9,
        formatter: function(v) { return timeAxisLabel(v, xView.labelByIndex); },
      },
      axisLine: { lineStyle: { color: '#dce4e7' } },
    },
    yAxis: {
      type: 'value',
      min: 0,
      axisLabel: {
        color: '#7a8a93', fontSize: 9,
        formatter: function(v) { return v >= 1000 ? (v/1000).toFixed(0)+'k' : v; }
      },
      splitLine: { lineStyle: { color: '#edf0f2', type: 'dashed' } },
    },
    series: [
      {
        name: '买入量', type: 'bar', stack: 'vol',
        data: visible.buyVols, barMaxWidth: 6,
        itemStyle: { color: 'rgba(25,140,128,0.65)' },
      },
      {
        name: '卖出量', type: 'bar', stack: 'vol',
        data: visible.sellVols, barMaxWidth: 6,
        itemStyle: { color: 'rgba(212,142,80,0.65)' },
      },
    ],
    legend: {
      data: ['买入量', '卖出量'],
      textStyle: { color: '#7a8a93', fontSize: 10 },
      top: 2, right: 10,
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#edf0f2',
      borderColor: '#dce4e7',
      textStyle: { color: '#32444e', fontSize: 11 },
      formatter: function(params) {
        return tooltipFormatter(params, xView.labelByIndex);
      },
    },
  }, true);
}


// ── Request frame data ────────────────────────────────────────────────
function fetchFrame(idx) {
  var requestId = ++frameRequestId;
  ensureCharts();
  setStatus('loading');
  var cfg = state.cfg;

  return window.marginFetch('/api/frame', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      frame_idx:      idx,
      window_size:    cfg.window_size,
      bins:           cfg.bins,
      time_layers:    cfg.time_layers,
      scheme:         state.scheme,
      hist_price_min: cfg.hist_price_min,
      hist_price_max: cfg.hist_price_max,
      ma_window:      cfg.ma_window,
    })
  })
  .then(async function(res) { var data = await res.json(); if (!res.ok) throw new Error(data.error); return data; })
  .then(function(data) {
    if (requestId !== frameRequestId) return;
    // Compute shared Y-axis range from chip data and price series
    var chipPriceLows  = data.chip.map(function(d) { return d.price_low; });
    var chipPriceHighs = data.chip.map(function(d) { return d.price_high; });
    var chipMin = Math.min.apply(null, chipPriceLows);
    var chipMax = Math.max.apply(null, chipPriceHighs);
    var priceMin = Math.min.apply(null, data.price_series.prices);
    var priceMax = Math.max.apply(null, data.price_series.prices);
    var sharedMin = Math.min(chipMin, priceMin) - 0.5;
    var sharedMax = Math.max(chipMax, priceMax) + 0.5;

    // User overrides take precedence if set
    var yMin = cfg.price_view_min != null ? cfg.price_view_min : sharedMin;
    var yMax = cfg.price_view_max != null ? cfg.price_view_max : sharedMax;

    var xView = buildXView(data.price_series, data.frame_idx);
    renderPrice(data.price_series, data.chip, data.latest_price, state.scheme, yMin, yMax, xView);
    renderVol(data.price_series, xView);

    document.getElementById('latestPrice').textContent =
      data.latest_price.toFixed(2);
    document.getElementById('frameLabel').textContent =
      idx + ' / ' + (data.total - 1);
    document.getElementById('progressBar').value = idx;

    setStatus('ok');
  })
  .catch(function(e) {
    setStatus('error');
    console.error('[DEBUG] fetchFrame error:', e);
  });
}


// ── Playback controls ──────────────────────────────────────────────────
function togglePlay() {
  state.playing = !state.playing;
  var btn = document.getElementById('btnPlay');
  btn.textContent = state.playing ? '⏸ 暂停' : '▶ 播放';
  btn.classList.toggle('active', state.playing);

  if (state.playing) {
    scheduleNext();
  } else {
    clearTimeout(state.timer);
  }
}

function scheduleNext() {
  if (!state.playing) return;
  state.timer = setTimeout(async function() {
    var step = parseInt(document.getElementById('cfgStep').value) || 3;
    state.frameIdx = Math.min(
      state.frameIdx + step,
      state.totalFrames - 1
    );
    await fetchFrame(state.frameIdx);

    if (!state.playing) return;
    if (state.frameIdx >= state.totalFrames - 1) {
      state.playing = false;
      document.getElementById('btnPlay').textContent = '▶ 播放';
      document.getElementById('btnPlay').classList.remove('active');
      return;
    }
    scheduleNext();
  }, parseInt(document.getElementById('cfgInterval').value) || 200);
}

function stepFrame(delta) {
  clearTimeout(state.timer);
  state.playing = false;
  document.getElementById('btnPlay').textContent = '▶ 播放';
  document.getElementById('btnPlay').classList.remove('active');

  state.frameIdx = Math.max(
    0, Math.min(state.frameIdx + delta, state.totalFrames - 1)
  );
  fetchFrame(state.frameIdx);
}

function seekFrame(val) {
  clearTimeout(state.timer);
  state.playing = false;
  document.getElementById('btnPlay').textContent = '▶ 播放';
  document.getElementById('btnPlay').classList.remove('active');
  state.frameIdx = parseInt(val);
  fetchFrame(state.frameIdx);
}

function resetFrame() {
  clearTimeout(state.timer);
  state.playing = false;
  state.frameIdx = 0;
  document.getElementById('btnPlay').textContent = '▶ 播放';
  document.getElementById('btnPlay').classList.remove('active');
  fetchFrame(0);
}


// ── Switch display schemes ──────────────────────────────────────────────────
function setScheme(s, el) {
  state.scheme = s;
  document.querySelectorAll('.scheme-btn')
          .forEach(function(b) { b.classList.remove('active'); });
  el.classList.add('active');
  document.getElementById('schemeDesc').textContent = schemeDesc(s);
  fetchFrame(state.frameIdx);
}


// ── Apply configuration ──────────────────────────────────────────────────
function applyConfig() {
  var g = function(id) { return document.getElementById(id).value; };
  var n = function(id, fallback) {
    if (fallback === undefined) fallback = null;
    var v = parseFloat(g(id));
    return isNaN(v) ? fallback : v;
  };

  state.cfg = {
    window_size:    parseInt(g('cfgWindow'))    || 200,
    bins:           parseInt(g('cfgBins'))      || 40,
    time_layers:    parseInt(g('cfgTimeLayers'))|| 10,
    hist_price_min: n('cfgHistMin'),
    hist_price_max: n('cfgHistMax'),
    ma_window:      parseInt(g('cfgMA'))        || 20,
    price_view_min: n('cfgViewMin'),
    price_view_max: n('cfgViewMax'),
    x_view_size:    normalizeViewSize(g('cfgXViewSize')),
    interval:       parseInt(g('cfgInterval'))  || 200,
    step:           parseInt(g('cfgStep'))      || 3,
  };

  fetchFrame(state.frameIdx);
}


// ── Regenerate data ──────────────────────────────────────────────
function regenerateData() {
  clearTimeout(state.timer);
  state.playing   = false;
  state.frameIdx  = 0;
  setStatus('loading');

  var nRec = parseInt(document.getElementById('cfgNRecords').value) || 1000;
  var seed = parseInt(document.getElementById('cfgSeed').value)     || 42;

  window.marginFetch('/api/regenerate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ n_records: nRec, seed: seed })
  })
  .then(async function(res) { var data = await res.json(); if (!res.ok) throw new Error(data.error); return data; })
  .then(function(data) {
    state.totalFrames = data.total_records;
    document.getElementById('progressBar').max = data.total_records - 1;
    document.getElementById('btnPlay').textContent = '▶ 播放';
    document.getElementById('btnPlay').classList.remove('active');
    fetchFrame(0);
  })
  .catch(function(e) {
    setStatus('error');
    console.error('[DEBUG] regenerateData error:', e);
  });
}


// ── Status indicator ──────────────────────────────────────────────────
function setStatus(s) {
  var dot  = document.getElementById('statusDot');
  var text = document.getElementById('statusText');
  var map  = {
    ok:      ['#198c80', '就绪'],
    loading: ['#c58c42', '加载中...'],
    error:   ['#d48e50', '错误'],
  };
  dot.style.background  = map[s][0];
  text.textContent      = map[s][1];
}


// ── Responsive resizing ─────────────────────────────────────────────
window.addEventListener('resize', function() {
  if (priceChartInst) priceChartInst.resize();
  if (volChartInst)   volChartInst.resize();
});


// ── Initialization ────────────────────────────────────────────────────
// This script is loaded with defer, so DOM is ready and all prior
// scripts (echarts CDN) are guaranteed to have executed.
if (typeof echarts !== 'undefined') {
  ensureCharts();
  fetchFrame(0);
} else {
  setStatus('error');
  document.getElementById('statusText').textContent = '图表资源加载失败，请检查网络并刷新';
}
