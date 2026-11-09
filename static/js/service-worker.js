/* global clients, self */

self.addEventListener("install", () => {
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
    let payload = {};
    try {
        payload = event.data ? event.data.json() : {};
    } catch (error) {
        payload = {};
    }

    const title = payload.title || "Session booking";
    const options = {
        body: payload.body || "",
        icon: payload.icon || "/static/images/logo.png",
        badge: payload.badge || "/static/images/logo.png",
        tag: payload.tag || undefined,
        data: {
            url: payload.url || "/notifications/",
        },
    };

    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const url = event.notification.data?.url || "/notifications/";
    const target = new URL(url, self.location.origin);

    event.waitUntil(
        clients
            .matchAll({ type: "window", includeUncontrolled: true })
            .then((clientList) => {
                for (const client of clientList) {
                    const clientUrl = new URL(client.url);
                    if (
                        clientUrl.origin === target.origin &&
                        clientUrl.pathname === target.pathname &&
                        "focus" in client
                    ) {
                        return client.focus();
                    }
                }
                if (clients.openWindow) {
                    return clients.openWindow(target.href);
                }
                return undefined;
            })
    );
});
