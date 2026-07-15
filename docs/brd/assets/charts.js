/**
 * FinFlow Global AaaS BRD — ECharts
 * 跨境电商出口规模趋势（2020-2024）
 */
document.addEventListener('DOMContentLoaded', function () {
  var dom = document.getElementById('chart-export-trend');
  if (!dom || typeof echarts === 'undefined') return;

  var chart = echarts.init(dom, null, { renderer: 'canvas' });

  var years = ['2020', '2021', '2022', '2023', '2024'];
  var values = [1.12, 1.39, 1.55, 1.84, 2.15]; // 单位：万亿元
  var growthRates = [null, 24.1, 11.5, 18.7, 16.9]; // 同比增长率 %

  var option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#0f172a',
      borderColor: '#334155',
      textStyle: { color: '#f8fafc', fontFamily: 'InstrumentSans, sans-serif', fontSize: 13 },
      formatter: function (params) {
        var bar = params[0];
        var line = params[1];
        var gHtml = line.value != null ? '<br/>同比增长：<b style="color:#d97706">' + line.value + '%</b>' : '';
        return '<b>' + bar.name + ' 年</b><br/>出口规模：<b style="color:#1e40af">' + bar.value + ' 万亿元</b>' + gHtml;
      }
    },
    grid: { top: 60, right: 50, bottom: 50, left: 70, containLabel: false },
    xAxis: {
      type: 'category',
      data: years,
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      axisTick: { show: false },
      axisLabel: { color: '#64748b', fontFamily: 'InstrumentSans, sans-serif', fontSize: 13, formatter: '{value} 年' }
    },
    yAxis: [
      {
        type: 'value',
        name: '出口规模（万亿元）',
        nameTextStyle: { color: '#64748b', fontFamily: 'InstrumentSans, sans-serif', fontSize: 12 },
        max: 2.8,
        splitLine: { lineStyle: { color: '#e2e8f0', type: 'dashed' } },
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: '#64748b', fontFamily: 'InstrumentSans, sans-serif', fontSize: 12 }
      },
      {
        type: 'value',
        name: '同比增长率（%）',
        nameTextStyle: { color: '#64748b', fontFamily: 'InstrumentSans, sans-serif', fontSize: 12 },
        max: 30,
        splitLine: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: '#64748b', fontFamily: 'InstrumentSans, sans-serif', fontSize: 12, formatter: '{value}%' }
      }
    ],
    series: [
      {
        name: '出口规模',
        type: 'bar',
        yAxisIndex: 0,
        barWidth: '36%',
        data: values,
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#1e40af' },
            { offset: 1, color: '#3b82f6' }
          ]),
          borderRadius: [4, 4, 0, 0]
        },
        label: {
          show: true,
          position: 'top',
          color: '#1e40af',
          fontFamily: 'InstrumentSans, sans-serif',
          fontWeight: 700,
          fontSize: 13,
          formatter: '{c}'
        }
      },
      {
        name: '同比增长率',
        type: 'line',
        yAxisIndex: 1,
        data: growthRates,
        smooth: true,
        symbol: 'circle',
        symbolSize: 7,
        lineStyle: { color: '#d97706', width: 2.5 },
        itemStyle: { color: '#d97706', borderWidth: 2, borderColor: '#ffffff' },
        label: {
          show: true,
          position: 'top',
          color: '#d97706',
          fontFamily: 'InstrumentSans, sans-serif',
          fontWeight: 600,
          fontSize: 12,
          formatter: function (p) { return p.value != null ? p.value + '%' : ''; }
        }
      }
    ]
  };

  chart.setOption(option);

  window.addEventListener('resize', function () { chart.resize(); });
});