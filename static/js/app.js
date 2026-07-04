// DATE
const now = new Date();

document.getElementById('dateNow').innerHTML =
    now.toDateString();

// CHART
var options = {

    chart: {
        type: 'line',
        height: 260,
        toolbar: {
            show: false
        },
        zoom: {
            enabled: false
        }
    },

    series: [

        {
            name: 'Medis',
            data: [300, 400, 350, 500, 600, 550, 580]
        },

        {
            name: 'Non-Medis',
            data: [150, 300, 250, 280, 320, 310, 330]
        }

    ],

    xaxis: {
        categories: ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul']
    },

    colors: ['#14A673', '#52d6a5'],

    stroke: {
        curve: 'smooth',
        width: 4
    },

    grid: {
        borderColor: '#e5e7eb'
    },

    dataLabels: {
        enabled: false
    },

    legend: {
        position: 'top'
    }

};

var chart = new ApexCharts(
    document.querySelector("#chart"),
    options
);

chart.render();