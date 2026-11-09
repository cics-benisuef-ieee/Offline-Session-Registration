/* global Notification, PushManager */

(function () {
    "use strict";

    const vapidMeta = document.querySelector('meta[name="vapid-public-key"]');
    const csrfMeta = document.querySelector('meta[name="csrf-token"]');
    const VAPID_PUBLIC_KEY = vapidMeta ? vapidMeta.content : "";
    const CSRF_TOKEN = csrfMeta ? csrfMeta.content : "";

    function urlBase64ToUint8Array(base64String) {
        const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
        const base64 = (base64String + padding)
            .replace(/-/g, "+")
            .replace(/_/g, "/");
        const rawData = window.atob(base64);
        const outputArray = new Uint8Array(rawData.length);
        for (let index = 0; index < rawData.length; index += 1) {
            outputArray[index] = rawData.charCodeAt(index);
        }
        return outputArray;
    }

    async function postJson(url, payload) {
        const response = await fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": CSRF_TOKEN,
            },
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            throw new Error(`Request failed with status ${response.status}`);
        }
        return response.json();
    }

    function setStatus(text) {
        document.querySelectorAll("[data-push-status]").forEach((element) => {
            element.textContent = text;
        });
    }

    function setButtons({ enable, disable }) {
        document.querySelectorAll("[data-push-enable]").forEach((element) => {
            element.hidden = !enable;
        });
        document.querySelectorAll("[data-push-disable]").forEach((element) => {
            element.hidden = !disable;
        });
    }

    function supported() {
        return (
            "Notification" in window &&
            "serviceWorker" in navigator &&
            "PushManager" in window
        );
    }

    async function registerServiceWorker() {
        if (!VAPID_PUBLIC_KEY) {
            return null;
        }
        return navigator.serviceWorker.register("/sw.js", { scope: "/" });
    }

    async function getSubscription() {
        const registration = await navigator.serviceWorker.ready;
        return registration.pushManager.getSubscription();
    }

    async function syncSubscription() {
        if (!VAPID_PUBLIC_KEY || !supported()) {
            return;
        }
        if (Notification.permission !== "granted") {
            return;
        }
        const subscription = await getSubscription();
        if (!subscription) {
            return;
        }
        await postJson(
            "/notifications/push/subscribe/",
            subscription.toJSON()
        );
    }

    async function enablePush() {
        if (!VAPID_PUBLIC_KEY) {
            setStatus("Browser notifications are not configured on this server.");
            return;
        }
        if (!supported()) {
            setStatus("This browser does not support push notifications.");
            return;
        }

        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
            setStatus("Browser permission was not granted.");
            await refresh();
            return;
        }

        await registerServiceWorker();
        const registration = await navigator.serviceWorker.ready;
        let subscription = await registration.pushManager.getSubscription();
        if (!subscription) {
            subscription = await registration.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY),
            });
        }
        await postJson(
            "/notifications/push/subscribe/",
            subscription.toJSON()
        );
        setStatus("Browser notifications are enabled on this device.");
        setButtons({ enable: false, disable: true });
    }

    async function disablePush() {
        const subscription = await getSubscription();
        if (subscription) {
            await postJson("/notifications/push/unsubscribe/", {
                endpoint: subscription.endpoint,
            });
            await subscription.unsubscribe();
        }
        setStatus("Browser notifications are disabled on this device.");
        setButtons({ enable: true, disable: false });
    }

    async function refresh() {
        if (!VAPID_PUBLIC_KEY) {
            setStatus("Browser notifications are not configured on this server.");
            setButtons({ enable: false, disable: false });
            return;
        }
        if (!supported()) {
            setStatus("This browser does not support push notifications.");
            setButtons({ enable: false, disable: false });
            return;
        }
        if (Notification.permission === "denied") {
            setStatus(
                "Browser notifications are blocked. Enable them in your browser settings."
            );
            setButtons({ enable: false, disable: false });
            return;
        }

        const subscription = await getSubscription();
        if (Notification.permission === "granted" && subscription) {
            setStatus("Browser notifications are enabled on this device.");
            setButtons({ enable: false, disable: true });
        } else {
            setStatus("Browser notifications are not enabled on this device.");
            setButtons({ enable: true, disable: false });
        }
    }

    document.addEventListener("DOMContentLoaded", async () => {
        if (VAPID_PUBLIC_KEY) {
            try {
                await registerServiceWorker();
                await syncSubscription();
            } catch (error) {
                console.warn("Push registration failed", error);
            }
        }

        document.querySelectorAll("[data-push-enable]").forEach((button) => {
            button.addEventListener("click", () => {
                enablePush().catch((error) => setStatus(error.message));
            });
        });
        document.querySelectorAll("[data-push-disable]").forEach((button) => {
            button.addEventListener("click", () => {
                disablePush().catch((error) => setStatus(error.message));
            });
        });

        await refresh().catch((error) => setStatus(error.message));
    });
})();
