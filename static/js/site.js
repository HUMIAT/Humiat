// Animações simples da HUMIAT
const elementos = document.querySelectorAll('.revelar');
const observador = new IntersectionObserver((entradas) => {
    entradas.forEach((entrada) => {
        if (entrada.isIntersecting) entrada.target.classList.add('visivel');
    });
}, { threshold: 0.12 });
elementos.forEach((el) => observador.observe(el));

// Fundo de partículas leve, sem biblioteca externa
const canvas = document.getElementById('particulas');
const contexto = canvas.getContext('2d');
let largura, altura, pontos;

function redimensionar() {
    largura = canvas.width = window.innerWidth;
    altura = canvas.height = window.innerHeight;
    pontos = Array.from({ length: Math.min(90, Math.floor(largura / 18)) }, () => ({
        x: Math.random() * largura,
        y: Math.random() * altura,
        vx: (Math.random() - 0.5) * 0.35,
        vy: (Math.random() - 0.5) * 0.35
    }));
}

function animar() {
    contexto.clearRect(0, 0, largura, altura);
    contexto.fillStyle = 'rgba(0,174,255,.75)';
    contexto.strokeStyle = 'rgba(0,174,255,.16)';
    pontos.forEach((ponto, i) => {
        ponto.x += ponto.vx;
        ponto.y += ponto.vy;
        if (ponto.x < 0 || ponto.x > largura) ponto.vx *= -1;
        if (ponto.y < 0 || ponto.y > altura) ponto.vy *= -1;
        contexto.beginPath();
        contexto.arc(ponto.x, ponto.y, 1.5, 0, Math.PI * 2);
        contexto.fill();
        for (let j = i + 1; j < pontos.length; j++) {
            const outro = pontos[j];
            const distancia = Math.hypot(ponto.x - outro.x, ponto.y - outro.y);
            if (distancia < 120) {
                contexto.globalAlpha = 1 - distancia / 120;
                contexto.beginPath();
                contexto.moveTo(ponto.x, ponto.y);
                contexto.lineTo(outro.x, outro.y);
                contexto.stroke();
                contexto.globalAlpha = 1;
            }
        }
    });
    requestAnimationFrame(animar);
}

window.addEventListener('resize', redimensionar);
redimensionar();
animar();
