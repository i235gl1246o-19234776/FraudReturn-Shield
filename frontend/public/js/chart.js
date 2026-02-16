
class ChartManager {
  constructor() {
    this.shapChart = null;
  }

  initShapChart(canvasId) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    this.shapChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: [],
        datasets: [{
          label: 'Вклад в риск',
          data: [],
          backgroundColor: [],
          borderRadius: 4,
        }]
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: false
          },
          tooltip: {
            callbacks: {
              label: (context) => {
                const value = context.raw;
                const sign = value >= 0 ? '+' : '';
                return `${sign}${(value * 100).toFixed(1)}% риска`;
              }
            }
          }
        },
        scales: {
          x: {
            beginAtZero: true,
            title: {
              display: true,
              text: 'Влияние на риск (%)'
            }
          }
        }
      }
    });
  }

  updateShapChart(shapValues) {
    if (!this.shapChart) return;

    const sorted = [...shapValues].sort((a, b) => 
      Math.abs(b.impact) - Math.abs(a.impact)
    ).slice(0, 5);

    this.shapChart.data.labels = sorted.map(s => {
      const names = {
        'account_age_days': 'Возраст аккаунта',
        'address_match': 'Совпадение адресов',
        'days_to_return': 'Дней до возврата',
        'order_amount': 'Сумма заказа',
        'return_rate': 'Доля возвратов'
      };
      return names[s.feature] || s.feature;
    });

    this.shapChart.data.datasets[0].data = sorted.map(s => s.impact * 100);
    this.shapChart.data.datasets[0].backgroundColor = sorted.map(s => 
      s.impact >= 0 ? 'rgba(239, 68, 68, 0.7)' : 'rgba(16, 185, 129, 0.7)'
    );

    this.shapChart.update('active');
  }

  updateGauge(score) {
    const gaugeFill = document.getElementById('gaugeFill');
    const riskScore = document.getElementById('riskScore');
    
    if (!gaugeFill || !riskScore) return;

    this.animateNumber(riskScore, score * 100, 1000);

    const circumference = 2 * Math.PI * 45;
    const offset = circumference - (score * circumference);
    gaugeFill.style.strokeDashoffset = offset;

    if (score <= 0.30) {
      gaugeFill.style.stroke = '#10b981';
    } else if (score <= 0.65) {
      gaugeFill.style.stroke = '#f59e0b';
    } else {
      gaugeFill.style.stroke = '#ef4444';
    }
  }

  animateNumber(element, target, duration) {
    const start = 0;
    const startTime = performance.now();

    const animate = (currentTime) => {
      const elapsed = currentTime - startTime;
      const progress = Math.min(elapsed / duration, 1);
      
      const easeOut = 1 - Math.pow(1 - progress, 3);
      const current = start + (target - start) * easeOut;
      
      element.textContent = Math.round(current);

      if (progress < 1) {
        requestAnimationFrame(animate);
      }
    };

    requestAnimationFrame(animate);
  }
}

const chartManager = new ChartManager();