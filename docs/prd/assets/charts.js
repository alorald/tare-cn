/**
 * FinFlow Global AaaS PRD — ECharts
 * 知识库文档按地区分布饼图
 */
document.addEventListener('DOMContentLoaded', function () {

  /* === 饼图：知识库文档按地区分布 === */
  var dom1 = document.getElementById('chart-kb-distribution');
  if (dom1 && typeof echarts !== 'undefined') {
    var chart1 = echarts.init(dom1, null, { renderer: 'canvas' });

    var option1 = {
      backgroundColor: 'transparent',
      title: {
        text: '知识库文档按地区分布',
        left: 'center',
        top: 10,
        textStyle: {
          color: '#0f172a',
          fontSize: 15,
          fontWeight: 700,
          fontFamily: 'InstrumentSans, sans-serif'
        }
      },
      tooltip: {
        trigger: 'item',
        backgroundColor: '#0f172a',
        borderColor: '#334155',
        textStyle: { color: '#f8fafc', fontFamily: 'InstrumentSans, sans-serif', fontSize: 13 },
        formatter: function (p) {
          return '<b>' + p.name + '</b><br/>文档数量：<b style="color:#1e40af">' + p.value + ' 份</b><br/>占比：' + p.percent + '%';
        }
      },
      legend: {
        orient: 'horizontal',
        bottom: 10,
        itemGap: 24,
        textStyle: { color: '#64748b', fontFamily: 'InstrumentSans, sans-serif', fontSize: 13 }
      },
      series: [
        {
          name: '文档分布',
          type: 'pie',
          radius: ['40%', '70%'],
          center: ['50%', '52%'],
          avoidLabelOverlap: true,
          itemStyle: {
            borderRadius: 6,
            borderColor: '#ffffff',
            borderWidth: 2
          },
          label: {
            show: true,
            color: '#0f172a',
            fontFamily: 'InstrumentSans, sans-serif',
            fontSize: 13,
            formatter: '{b}\n{c} 份 ({d}%)'
          },
          labelLine: {
            lineStyle: { color: '#94a3b8' }
          },
          emphasis: {
            label: { fontSize: 15, fontWeight: 700 },
            itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.15)' }
          },
          data: [
            { value: 12, name: '欧洲', itemStyle: { color: '#1e40af' } },
            { value: 7, name: '北美', itemStyle: { color: '#3b82f6' } },
            { value: 6, name: '亚太', itemStyle: { color: '#d97706' } },
            { value: 4, name: '新兴市场', itemStyle: { color: '#64748b' } }
          ]
        }
      ]
    };

    chart1.setOption(option1);
    window.addEventListener('resize', function () { chart1.resize(); });
  }

  /* === 饼图副本：用于 AI 专项章节 === */
  var dom2 = document.getElementById('chart-kb-distribution-2');
  if (dom2 && typeof echarts !== 'undefined') {
    var chart2 = echarts.init(dom2, null, { renderer: 'canvas' });

    var option2 = {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item',
        backgroundColor: '#0f172a',
        borderColor: '#334155',
        textStyle: { color: '#f8fafc', fontFamily: 'InstrumentSans, sans-serif', fontSize: 13 },
        formatter: function (p) {
          return '<b>' + p.name + '</b><br/>文档数量：<b style="color:#1e40af">' + p.value + ' 份</b><br/>占比：' + p.percent + '%';
        }
      },
      legend: {
        orient: 'horizontal',
        bottom: 10,
        itemGap: 24,
        textStyle: { color: '#64748b', fontFamily: 'InstrumentSans, sans-serif', fontSize: 13 }
      },
      series: [
        {
          name: '文档分布',
          type: 'pie',
          radius: ['40%', '70%'],
          center: ['50%', '50%'],
          avoidLabelOverlap: true,
          itemStyle: {
            borderRadius: 6,
            borderColor: '#ffffff',
            borderWidth: 2
          },
          label: {
            show: true,
            color: '#0f172a',
            fontFamily: 'InstrumentSans, sans-serif',
            fontSize: 13,
            formatter: '{b}\n{c} 份 ({d}%)'
          },
          labelLine: {
            lineStyle: { color: '#94a3b8' }
          },
          emphasis: {
            label: { fontSize: 15, fontWeight: 700 },
            itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.15)' }
          },
          data: [
            { value: 12, name: '欧洲', itemStyle: { color: '#1e40af' } },
            { value: 7, name: '北美', itemStyle: { color: '#3b82f6' } },
            { value: 6, name: '亚太', itemStyle: { color: '#d97706' } },
            { value: 4, name: '新兴市场', itemStyle: { color: '#64748b' } }
          ]
        }
      ]
    };

    chart2.setOption(option2);
    window.addEventListener('resize', function () { chart2.resize(); });
  }
});
