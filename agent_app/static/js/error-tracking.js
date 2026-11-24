/**
 * Error Tracking Client - Captures user actions and sends logs on error.
 * Logs stored locally in user's browser, sent to server only when errors occur.
 * 
 * Features:
 * - Ring buffer (max 500 entries)
 * - Captures clicks, form submits, fetch/XHR, navigation, console errors
 * - 30-step checkpoints for sync points
 * - Sends full context on error via sendBeacon
 * - Session persistence across page loads
 */

(function() {
    'use strict';

    const MAX_LOGS = 500;
    const CHECKPOINT_INTERVAL = 30;
    const STORAGE_KEY = 'emog_error_logs';
    const SESSION_KEY = 'emog_error_session';

    class ErrorTracker {
        constructor() {
            this.logs = [];
            this.checkpoints = [];
            this.stepCount = 0;
            this.sessionId = this.getOrCreateSessionId();
            this.requestId = null;
            this.initialized = false;
            this.isSending = false;
        }

        init() {
            if (this.initialized) return;
            this.initialized = true;

            // Restore logs from sessionStorage
            this.restoreLogs();

            // Capture request ID from response header
            this.captureRequestId();

            // Capture unhandled errors
            window.addEventListener('error', (e) => this.captureError(e.error, 'unhandled'));
            window.addEventListener('unhandledrejection', (e) => this.captureError(e.reason, 'promise'));

            // Capture console.error
            this.patchConsole();

            // Capture user actions
            this.captureUserActions();

            // Capture fetch/XHR
            this.patchFetch();
            this.patchXHR();

            // Capture navigation
            this.captureNavigation();

            // Expose for debugging
            window.errorTracker = this;

            // Log initialization
            this.addLog('init', {
                sessionId: this.sessionId,
                url: window.location.href,
                userAgent: navigator.userAgent,
                screen: { width: screen.width, height: screen.height },
                viewport: { width: window.innerWidth, height: window.innerHeight },
            });
        }

        getOrCreateSessionId() {
            let sessionId = sessionStorage.getItem(SESSION_KEY);
            if (!sessionId) {
                sessionId = 'sess_' + Date.now().toString(36) + '_' + Math.random().toString(36).substr(2, 9);
                sessionStorage.setItem(SESSION_KEY, sessionId);
            }
            return sessionId;
        }

        captureRequestId() {
            // Try to get from meta tag or header
            const meta = document.querySelector('meta[name="request-id"]');
            if (meta) {
                this.requestId = meta.content;
            }
            // Also check for header via a global (set by middleware)
            if (window.__REQUEST_ID__) {
                this.requestId = window.__REQUEST_ID__;
            }
        }

        restoreLogs() {
            try {
                const stored = sessionStorage.getItem(STORAGE_KEY);
                if (stored) {
                    const data = JSON.parse(stored);
                    this.logs = data.logs || [];
                    this.checkpoints = data.checkpoints || [];
                    this.stepCount = data.stepCount || 0;
                    // Limit restored logs
                    if (this.logs.length > MAX_LOGS) {
                        this.logs = this.logs.slice(-MAX_LOGS);
                    }
                }
            } catch (e) {
                // Ignore parse errors
            }
        }

        saveLogs() {
            try {
                sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
                    logs: this.logs,
                    checkpoints: this.checkpoints,
                    stepCount: this.stepCount,
                    sessionId: this.sessionId,
                    updatedAt: new Date().toISOString(),
                }));
            } catch (e) {
                // Ignore quota errors
            }
        }

        addLog(type, data) {
            const entry = {
                timestamp: new Date().toISOString(),
                type,
                data: this.sanitizeData(data),
                step: ++this.stepCount,
                sessionId: this.sessionId,
                requestId: this.requestId,
                url: window.location.href,
            };

            this.logs.push(entry);

            // Ring buffer
            if (this.logs.length > MAX_LOGS) {
                this.logs.shift();
            }

            // Checkpoint every 30 steps
            if (this.stepCount % CHECKPOINT_INTERVAL === 0) {
                this.createCheckpoint();
            }

            this.saveLogs();
        }

        createCheckpoint() {
            this.checkpoints.push({
                step: this.stepCount,
                logsCount: this.logs.length,
                timestamp: new Date().toISOString(),
                url: window.location.href,
            });
            // Keep last 20 checkpoints
            if (this.checkpoints.length > 20) {
                this.checkpoints.shift();
            }
            this.saveLogs();
        }

        sanitizeData(data) {
            if (data === null || data === undefined) return data;
            if (typeof data !== 'object') return data;

            const sensitiveKeys = ['password', 'token', 'secret', 'key', 'authorization', 'csrf', 'cvv', 'ssn', 'api_key', 'apikey'];
            const sanitized = Array.isArray(data) ? [] : {};

            for (const [key, value] of Object.entries(data)) {
                const lowerKey = key.toLowerCase();
                if (sensitiveKeys.some(s => lowerKey.includes(s))) {
                    sanitized[key] = '[REDACTED]';
                } else if (typeof value === 'object' && value !== null) {
                    sanitized[key] = this.sanitizeData(value);
                } else {
                    sanitized[key] = value;
                }
            }

            return sanitized;
        }

        captureError(error, source) {
            // Don't capture if already sending
            if (this.isSending) return;

            const errorMessage = error?.message || String(error);
            const errorStack = error?.stack;

            // Skip if it's a known non-error (like script loading failures for extensions)
            if (this.shouldIgnoreError(errorMessage, errorStack)) return;

            this.isSending = true;

            const payload = {
                sessionId: this.sessionId,
                requestId: this.requestId,
                error: {
                    message: errorMessage,
                    stack: errorStack,
                    source,
                    name: error?.name,
                },
                logs: this.logs,
                checkpoints: this.checkpoints,
                userAgent: navigator.userAgent,
                url: window.location.href,
                viewport: { width: window.innerWidth, height: window.innerHeight },
                timestamp: new Date().toISOString(),
            };

            // Send via sendBeacon (reliable, doesn't block page unload)
            const sent = this.sendReport(payload);

            if (!sent) {
                // Fallback to fetch
                this.sendReportFetch(payload);
            }

            // Reset sending flag after a delay
            setTimeout(() => { this.isSending = false; }, 5000);
        }

        shouldIgnoreError(message, stack) {
            const ignorePatterns = [
                'Script error',
                'ResizeObserver loop limit exceeded',
                'Non-Error promise rejection captured',
                'Network request failed',
                'Failed to load resource',
                'Extension',
                'content-script',
                'userscript',
            ];

            const text = (message || '') + ' ' + (stack || '');
            return ignorePatterns.some(p => text.includes(p));
        }

        sendReport(payload) {
            try {
                const blob = new Blob([JSON.stringify(payload)], { type: 'application/json' });
                return navigator.sendBeacon('/api/logs/error/', blob);
            } catch (e) {
                return false;
            }
        }

        async sendReportFetch(payload) {
            try {
                await fetch('/api/logs/error/', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                    keepalive: true,
                });
            } catch (e) {
                // Silent fail - logs stay in sessionStorage for next attempt
            }
        }

        patchConsole() {
            const originalError = console.error.bind(console);
            console.error = (...args) => {
                this.addLog('console_error', {
                    args: args.map(arg => {
                        if (arg instanceof Error) return { message: arg.message, stack: arg.stack, name: arg.name };
                        if (typeof arg === 'object') return JSON.stringify(arg);
                        return String(arg);
                    }),
                });
                originalError.apply(console, args);
            };
        }

        captureUserActions() {
            // Clicks
            document.addEventListener('click', (e) => {
                const target = e.target;
                const tagName = target.tagName.toLowerCase();
                const id = target.id || '';
                const classes = target.className || '';
                const text = target.textContent ? target.textContent.trim().slice(0, 100) : '';
                const href = target.href || '';

                this.addLog('click', {
                    tag: tagName,
                    id,
                    classes: classes.slice(0, 200),
                    text: text.slice(0, 100),
                    href: href.slice(0, 200),
                    x: e.clientX,
                    y: e.clientY,
                });
            }, true); // Capture phase

            // Form submits
            document.addEventListener('submit', (e) => {
                const form = e.target;
                const formData = new FormData(form);
                const fields = {};
                for (const [key, value] of formData.entries()) {
                    fields[key] = value.toString().slice(0, 200);
                }

                this.addLog('form_submit', {
                    action: form.action || window.location.href,
                    method: form.method || 'GET',
                    fields: this.sanitizeData(fields),
                });
            }, true);

            // Input changes (debounced)
            let inputTimer;
            document.addEventListener('input', (e) => {
                clearTimeout(inputTimer);
                inputTimer = setTimeout(() => {
                    const target = e.target;
                    if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT') {
                        this.addLog('input_change', {
                            tag: target.tagName.toLowerCase(),
                            type: target.type || '',
                            name: target.name || '',
                            id: target.id || '',
                            valueLength: target.value?.length || 0,
                        });
                    }
                }, 500);
            }, true);
        }

        patchFetch() {
            const originalFetch = window.fetch.bind(window);
            window.fetch = async (url, options = {}) => {
                const startTime = Date.now();
                const requestId = 'req_' + Date.now().toString(36) + '_' + Math.random().toString(36).substr(2, 5);

                this.addLog('fetch_request', {
                    requestId,
                    url: String(url).slice(0, 300),
                    method: options.method || 'GET',
                });

                try {
                    const response = await originalFetch(url, options);
                    this.addLog('fetch_response', {
                        requestId,
                        status: response.status,
                        ok: response.ok,
                        durationMs: Date.now() - startTime,
                    });
                    return response;
                } catch (error) {
                    this.addLog('fetch_error', {
                        requestId,
                        error: error.message,
                        durationMs: Date.now() - startTime,
                    });
                    throw error;
                }
            };
        }

        patchXHR() {
            const originalOpen = XMLHttpRequest.prototype.open;
            const originalSend = XMLHttpRequest.prototype.send;

            XMLHttpRequest.prototype.open = function(method, url) {
                this._emogMethod = method;
                this._emogUrl = url;
                this._emogRequestId = 'xhr_' + Date.now().toString(36) + '_' + Math.random().toString(36).substr(2, 5);
                this._emogStartTime = Date.now();

                window.errorTracker?.addLog('xhr_request', {
                    requestId: this._emogRequestId,
                    url: String(url).slice(0, 300),
                    method,
                });

                return originalOpen.apply(this, arguments);
            };

            XMLHttpRequest.prototype.send = function(body) {
                const self = this;
                this.addEventListener('load', function() {
                    window.errorTracker?.addLog('xhr_response', {
                        requestId: self._emogRequestId,
                        status: self.status,
                        durationMs: Date.now() - self._emogStartTime,
                    });
                });
                this.addEventListener('error', function() {
                    window.errorTracker?.addLog('xhr_error', {
                        requestId: self._emogRequestId,
                        durationMs: Date.now() - self._emogStartTime,
                    });
                });
                return originalSend.apply(this, arguments);
            };
        }

        captureNavigation() {
            // SPA navigation (hashchange, popstate)
            window.addEventListener('hashchange', () => {
                this.addLog('navigation', { type: 'hashchange', url: window.location.href });
            });

            window.addEventListener('popstate', () => {
                this.addLog('navigation', { type: 'popstate', url: window.location.href });
            });

            // Page visibility
            document.addEventListener('visibilitychange', () => {
                this.addLog('visibility', { hidden: document.hidden });
            });

            // Before unload - create final checkpoint
            window.addEventListener('beforeunload', () => {
                this.createCheckpoint();
            });
        }

        // Public API
        getLogs() {
            return [...this.logs];
        }

        getCheckpoints() {
            return [...this.checkpoints];
        }

        clearLogs() {
            this.logs = [];
            this.checkpoints = [];
            this.stepCount = 0;
            this.saveLogs();
        }

        // Manual error reporting
        reportError(message, details = {}) {
            const error = new Error(message);
            error.details = details;
            this.captureError(error, 'manual');
        }
    }

    // Auto-initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            window.errorTracker = new ErrorTracker();
            window.errorTracker.init();
        });
    } else {
        window.errorTracker = new ErrorTracker();
        window.errorTracker.init();
    }
})();