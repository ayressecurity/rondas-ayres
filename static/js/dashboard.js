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
