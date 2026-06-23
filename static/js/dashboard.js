// JS del dashboard Rondas.
// Sidebar off-canvas en móvil/tablet: la hamburguesa abre/cierra el menú.
(function () {
    "use strict";

    var BP_ESCRITORIO = 1024; // sobre este ancho la sidebar es fija (no off-canvas)

    var toggle = document.getElementById("menuToggle");
    var overlay = document.getElementById("overlaySidebar");
    var sidebar = document.getElementById("sidebar");
    var body = document.body;

    if (!toggle || !sidebar) {
        return; // usuario sin sesión: no hay menú que controlar
    }

    function abrir() {
        body.classList.add("menu-abierto");
        toggle.setAttribute("aria-expanded", "true");
        if (overlay) { overlay.hidden = false; }
    }

    function cerrar() {
        body.classList.remove("menu-abierto");
        toggle.setAttribute("aria-expanded", "false");
        if (overlay) { overlay.hidden = true; }
    }

    function alternar() {
        if (body.classList.contains("menu-abierto")) {
            cerrar();
        } else {
            abrir();
        }
    }

    toggle.addEventListener("click", alternar);

    if (overlay) {
        overlay.addEventListener("click", cerrar);
    }

    // Cerrar al tocar un enlace del menú (en móvil el menú tapa el contenido).
    sidebar.addEventListener("click", function (e) {
        if (e.target.closest("a")) {
            cerrar();
        }
    });

    // Cerrar con la tecla Escape.
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
            cerrar();
        }
    });

    // Al volver a tamaño escritorio, limpiamos el estado móvil.
    var t;
    window.addEventListener("resize", function () {
        clearTimeout(t);
        t = setTimeout(function () {
            if (window.innerWidth > BP_ESCRITORIO) {
                cerrar();
            }
        }, 150);
    });
})();

// Modal de QR (checkpoints): muestra el PNG sin salir de la lista.
// Si no hay JS, el enlace abre el PNG en una pestaña (target="_blank").
(function () {
    "use strict";

    var modal = document.getElementById("qrModal");
    if (!modal) { return; }

    var img = document.getElementById("qrModalImg");
    var nombre = document.getElementById("qrModalNombre");
    var descargar = document.getElementById("qrModalDescargar");
    var enlaces = document.querySelectorAll(".accion-qr");

    function abrir(url, nombreCp) {
        img.src = url;
        nombre.textContent = nombreCp || "";
        descargar.href = url + (url.indexOf("?") === -1 ? "?" : "&") + "descargar=1";
        modal.hidden = false;
    }

    function cerrar() {
        modal.hidden = true;
        img.src = "";
    }

    enlaces.forEach(function (a) {
        a.addEventListener("click", function (e) {
            e.preventDefault();
            abrir(a.getAttribute("data-qr-url"), a.getAttribute("data-qr-nombre"));
        });
    });

    modal.addEventListener("click", function (e) {
        if (e.target.hasAttribute("data-qr-cerrar")) { cerrar(); }
    });

    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !modal.hidden) { cerrar(); }
    });
})();

// Modal de mapa (checkpoints): embebe Google Maps en la posición del punto.
// El src lo arma el backend (vista 'mapa') leyendo lat/lng de la BD.
// Sin JS, el enlace abre el mapa en una pestaña (target="_blank").
(function () {
    "use strict";

    var modal = document.getElementById("mapaModal");
    if (!modal) { return; }

    var iframe = document.getElementById("mapaModalIframe");
    var nombre = document.getElementById("mapaModalNombre");
    var enlaces = document.querySelectorAll(".accion-mapa");

    function abrir(url, nombreCp) {
        iframe.src = url;
        nombre.textContent = nombreCp || "";
        modal.hidden = false;
    }

    function cerrar() {
        modal.hidden = true;
        iframe.src = "";   // libera el mapa al cerrar
    }

    enlaces.forEach(function (a) {
        a.addEventListener("click", function (e) {
            e.preventDefault();
            abrir(a.getAttribute("data-mapa-url"), a.getAttribute("data-mapa-nombre"));
        });
    });

    modal.addEventListener("click", function (e) {
        if (e.target.hasAttribute("data-mapa-cerrar")) { cerrar(); }
    });

    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !modal.hidden) { cerrar(); }
    });
})();

// Tabla "Objetivos": seleccionar instalación haciendo clic en la fila.
(function () {
    "use strict";

    var filas = document.querySelectorAll(".fila-seleccionable[data-form]");

    filas.forEach(function (fila) {
        var form = document.getElementById(fila.getAttribute("data-form"));
        if (!form) { return; }

        function enviar() { form.submit(); }

        fila.addEventListener("click", function (e) {
            // Si se clickeó el propio botón/enlace, dejamos su acción normal.
            if (e.target.closest("button") || e.target.closest("a")) { return; }
            enviar();
        });

        // Accesible por teclado: Enter o Espacio sobre la fila enfocada.
        fila.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                enviar();
            }
        });
    });
})();
