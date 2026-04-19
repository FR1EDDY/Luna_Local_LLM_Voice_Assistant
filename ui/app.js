// ─── Simple inline markdown renderer ─────────────────────────────────────────
function renderMarkdown(md) {
    if (!md) return '';
    let html = md
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        // Headings
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        .replace(/^# (.+)$/gm, '<h1>$1</h1>')
        // Bold & italic
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        // Checkboxes
        .replace(/^- \[ \] (.+)$/gm, '<div class="check-item"><input type="checkbox"><span>$1</span></div>')
        .replace(/^- \[x\] (.+)$/gm, '<div class="check-item done"><input type="checkbox" checked><span>$1</span></div>')
        // Lists
        .replace(/^- (.+)$/gm, '<li>$1</li>')
        .replace(/^(\d+)\. (.+)$/gm, '<li>$2</li>')
        // Horizontal rules
        .replace(/^---+$/gm, '<hr>')
        // Code blocks
        .replace(/```[\s\S]*?```/g, m => '<pre>' + m.slice(3, -3) + '</pre>')
        // Inline code
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        // Paragraphs (double newlines)
        .replace(/\n\n+/g, '</p><p>')
        .replace(/\n/g, '<br>');
    // Wrap consecutive <li> in <ul>
    html = html.replace(/(<li>.*?<\/li>)+/gs, m => '<ul>' + m + '</ul>');
    return '<p>' + html + '</p>';
}

// ─── State ────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    const btnMic      = document.getElementById('btn-mic');
    const container   = document.getElementById('visualizer-container');
    const statusH2    = document.querySelector('#status-text h2');
    const statusP     = document.querySelector('#status-text p');
    const chatInput   = document.getElementById('chat-input');
    const btnSend     = document.getElementById('btn-send');

    // ─── Window controls ──────────────────────────────────────────────────────
    document.querySelector('.dot.close').addEventListener('click', () => {
        if (window.pywebview && pywebview.api) pywebview.api.close_window();
    });
    document.querySelector('.dot.min').addEventListener('click', () => {
        if (window.pywebview && pywebview.api) pywebview.api.minimize_window();
    });

    let currentState = 'idle';

    function setState(newState) {
        currentState = newState;
        container.classList.remove('idle', 'waking', 'listening', 'thinking', 'speaking');
        container.classList.add(newState);

        if (newState === 'idle') {
            statusH2.innerText = 'Luna is sleeping...';
            statusP.innerText  = 'Click the mic or type below';
            btnMic.classList.remove('active');
            document.documentElement.style.setProperty('--mic-level', '0');
        } else if (newState === 'waking') {
            statusH2.innerText = 'Luna is waking...';
            statusP.innerText  = 'Just a moment';
            btnMic.classList.add('active');
        } else if (newState === 'listening') {
            statusH2.innerText = 'Luna is listening...';
            statusP.innerText  = 'Ask me anything.';
            btnMic.classList.add('active');
        } else if (newState === 'thinking') {
            statusH2.innerText = 'Luna is thinking...';
            statusP.innerText  = 'Processing your message';
            btnMic.classList.remove('active');
            document.documentElement.style.setProperty('--mic-level', '0');
            const lunaEl = document.getElementById('transcript-luna-current');
            if (lunaEl) lunaEl.innerText = '';
        } else if (newState === 'speaking') {
            statusH2.innerText = 'Luna is answering...';
            statusP.innerText  = '';
            btnMic.classList.remove('active');
        }
    }

    // ─── Mic button ───────────────────────────────────────────────────────────
    btnMic.addEventListener('click', () => {
        if (currentState === 'idle') {
            setState('listening');
            if (window.pywebview && pywebview.api) {
                pywebview.api.start_listening();
            } else {
                setTimeout(() => {
                    setState('thinking');
                    setTimeout(() => { setState('speaking'); setTimeout(() => setState('idle'), 3000); }, 2000);
                }, 2000);
            }
        } else {
            setState('idle');
            if (window.pywebview && pywebview.api) pywebview.api.stop_listening();
        }
    });

    // ─── Stop button ─────────────────────────────────────────────────────────
    document.getElementById('btn-stop').addEventListener('click', () => {
        setState('idle');
        if (window.pywebview && pywebview.api) pywebview.api.stop_listening();
    });

    // ─── Text chat send ───────────────────────────────────────────────────────
    function sendChatText() {
        const text = (chatInput.value || '').trim();
        if (!text) return;
        chatInput.value = '';
        if (window.pywebview && pywebview.api) {
            pywebview.api.send_text(text);
        } else {
            // Browser demo mode
            setState('thinking');
            window.external_set_transcript(text, '');
            setTimeout(() => {
                setState('speaking');
                window.external_set_transcript(text, 'This is a demo response from Luna.');
                setTimeout(() => setState('idle'), 3000);
            }, 1500);
        }
    }

    btnSend.addEventListener('click', sendChatText);
    chatInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatText(); }
    });

    // ─── Luna voice volume (pywebview API) ─────────────────────────────────
    const voiceSlider = document.getElementById('luna-voice-volume');
    const voicePctEl = document.getElementById('luna-voice-volume-pct');

    function syncVoiceVolumeDisplay(v) {
        if (!voicePctEl || !voiceSlider) return;
        voicePctEl.innerText = `${v}`;
        voiceSlider.style.setProperty('--val', `${v}%`);
        if (voiceSlider.parentElement) {
            voiceSlider.parentElement.style.setProperty('--val', `${v}%`);
        }
    }

    async function loadVoiceVolumeFromBackend() {
        if (!voiceSlider || !window.pywebview || !pywebview.api || !pywebview.api.get_luna_voice_volume) return;
        try {
            const v = await pywebview.api.get_luna_voice_volume();
            const n = Math.max(0, Math.min(300, parseInt(v, 10) || 135));
            const scaled = Math.round(n / 3);
            voiceSlider.value = String(scaled);
            syncVoiceVolumeDisplay(scaled);
        } catch (e) { /* demo mode */ }
    }

    function bindVoiceVolume() {
        if (!voiceSlider) return;
        syncVoiceVolumeDisplay(voiceSlider.value);
        voiceSlider.addEventListener('input', () => {
            const v = parseInt(voiceSlider.value, 10) || 0;
            syncVoiceVolumeDisplay(v);
            if (window.pywebview && pywebview.api && pywebview.api.set_luna_voice_volume) {
                pywebview.api.set_luna_voice_volume(v * 3);
            }
        });
    }
    bindVoiceVolume();
    loadVoiceVolumeFromBackend();
    window.addEventListener('pywebviewready', loadVoiceVolumeFromBackend);

    // ─── Plans panel ─────────────────────────────────────────────────────────
    let currentPlanName = null;

    document.getElementById('btn-plans').addEventListener('click', openPlansPanel);
    document.getElementById('btn-close-plans').addEventListener('click', closePlansPanel);
    document.getElementById('btn-back-plans').addEventListener('click', showPlansList);
    document.getElementById('btn-save-plan').addEventListener('click', saveCurrentPlan);

    // Tab switching
    document.querySelectorAll('.plan-tab[data-tab]').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.plan-tab[data-tab]').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const tab = btn.dataset.tab;
            document.getElementById('plan-tab-preview').classList.toggle('hidden', tab !== 'preview');
            document.getElementById('plan-tab-edit').classList.toggle('hidden', tab !== 'edit');
        });
    });

    function openPlansPanel() {
        document.getElementById('plans-panel').classList.remove('hidden');
        showPlansList();
    }

    function closePlansPanel() {
        document.getElementById('plans-panel').classList.add('hidden');
    }

    async function showPlansList() {
        document.getElementById('plans-list-view').classList.remove('hidden');
        document.getElementById('plan-editor-view').classList.add('hidden');
        document.getElementById('btn-back-plans').classList.add('hidden');
        document.getElementById('plans-title').innerText = 'Plans';
        currentPlanName = null;

        let names = [];
        if (window.pywebview && pywebview.api) {
            try { names = await pywebview.api.list_plans() || []; } catch(e) {}
        }

        const listEl = document.getElementById('plans-list');
        const emptyEl = document.getElementById('plans-empty');
        listEl.innerHTML = '';

        if (!names || names.length === 0) {
            emptyEl.classList.remove('hidden');
            return;
        }
        emptyEl.classList.add('hidden');
        names.forEach(name => {
            const item = document.createElement('div');
            item.className = 'plan-list-item';
            item.innerHTML = `<span class="plan-name">${name.replace(/-/g, ' ')}</span>
                              <button class="btn-delete-plan" data-name="${name}">🗑</button>`;
            item.querySelector('.plan-name').addEventListener('click', () => openPlan(name));
            item.querySelector('.btn-delete-plan').addEventListener('click', async (e) => {
                e.stopPropagation();
                if (window.pywebview && pywebview.api) {
                    await pywebview.api.delete_plan(name);
                    showPlansList();
                }
            });
            listEl.appendChild(item);
        });
    }

    async function openPlan(name) {
        currentPlanName = name;
        window._currentPlanName = name;
        document.getElementById('plans-list-view').classList.add('hidden');
        document.getElementById('plan-editor-view').classList.remove('hidden');
        document.getElementById('btn-back-plans').classList.remove('hidden');
        document.getElementById('plans-title').innerText = name.replace(/-/g, ' ');

        // Reset to preview tab
        document.querySelectorAll('.plan-tab[data-tab]').forEach(b => b.classList.remove('active'));
        document.querySelector('.plan-tab[data-tab="preview"]').classList.add('active');
        document.getElementById('plan-tab-preview').classList.remove('hidden');
        document.getElementById('plan-tab-edit').classList.add('hidden');

        let content = '';
        if (window.pywebview && pywebview.api) {
            try { content = await pywebview.api.get_plan(name) || ''; } catch(e) {}
        }
        document.getElementById('plan-editor-textarea').value = content;
        updatePlanPreview(content);
    }

    function updatePlanPreview(md) {
        document.getElementById('plan-preview').innerHTML = renderMarkdown(md);
    }

    document.getElementById('plan-editor-textarea').addEventListener('input', function() {
        updatePlanPreview(this.value);
    });

    async function saveCurrentPlan() {
        const name = currentPlanName || window._currentPlanName;
        if (!name) return;
        currentPlanName = name;
        const content = document.getElementById('plan-editor-textarea').value;
        if (window.pywebview && pywebview.api) {
            await pywebview.api.save_plan(name, content);
        }
        updatePlanPreview(content);
        const btn = document.getElementById('btn-save-plan');
        btn.innerText = 'Saved ✓';
        setTimeout(() => { btn.innerText = 'Save'; }, 1500);
    }

    // ─── Manual height resize via drag grip ───────────────────────────────────
    // Width stays fixed (pywebview.api.resize_height locks 420). Drag the grip
    // at the bottom-right corner to grow/shrink the window vertically.
    const grip = document.getElementById('resize-grip');
    if (grip) {
        const MIN_H = 380;
        const MAX_H = 1200;
        let dragging = false;
        let startY = 0;
        let startH = 0;
        let pendingH = null;
        let rafId = 0;

        function flush() {
            rafId = 0;
            if (pendingH !== null && window.pywebview && pywebview.api && pywebview.api.resize_height) {
                const h = pendingH;
                pendingH = null;
                pywebview.api.resize_height(h);
            }
        }

        function onMove(e) {
            if (!dragging) return;
            e.preventDefault();
            const dy = e.clientY - startY;
            const next = Math.max(MIN_H, Math.min(MAX_H, Math.round(startH + dy)));
            pendingH = next;
            if (!rafId) rafId = requestAnimationFrame(flush);
        }

        function onUp() {
            if (!dragging) return;
            dragging = false;
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            document.removeEventListener('mousemove', onMove, true);
            document.removeEventListener('mouseup', onUp, true);
            if (rafId) { cancelAnimationFrame(rafId); rafId = 0; flush(); }
        }

        grip.addEventListener('mousedown', (e) => {
            if (e.button !== 0) return;
            dragging = true;
            startY = e.clientY;
            startH = window.innerHeight;
            document.body.style.cursor = 'ns-resize';
            document.body.style.userSelect = 'none';
            document.addEventListener('mousemove', onMove, true);
            document.addEventListener('mouseup', onUp, true);
            e.preventDefault();
            e.stopPropagation();
        });
    }

    // ─── Top edge resize grip ────────────────────────────────────────────────
    // Drag the thin strip at the very top to grow/shrink upward. The bottom
    // edge stays anchored. Position math runs in Python because window.screenX
    // / window.screenY inside the pywebview WKWebView return 0.
    const gripTop = document.getElementById('resize-grip-top');
    if (gripTop) {
        let dragging = false;
        let startScreenY = 0;
        let pendingDy = null;
        let rafId = 0;

        function flush() {
            rafId = 0;
            if (pendingDy !== null && window.pywebview && pywebview.api && pywebview.api.resize_top) {
                const d = pendingDy;
                pendingDy = null;
                pywebview.api.resize_top(d);
            }
        }

        function onMove(e) {
            if (!dragging) return;
            e.preventDefault();
            pendingDy = e.screenY - startScreenY;
            if (!rafId) rafId = requestAnimationFrame(flush);
        }

        function onUp() {
            if (!dragging) return;
            dragging = false;
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            document.removeEventListener('mousemove', onMove, true);
            document.removeEventListener('mouseup', onUp, true);
            if (rafId) { cancelAnimationFrame(rafId); rafId = 0; flush(); }
            if (window.pywebview && pywebview.api && pywebview.api.end_resize_top) {
                pywebview.api.end_resize_top();
            }
        }

        gripTop.addEventListener('mousedown', (e) => {
            if (e.button !== 0) return;
            dragging = true;
            startScreenY = e.screenY;
            pendingDy = null;
            if (window.pywebview && pywebview.api && pywebview.api.start_resize_top) {
                pywebview.api.start_resize_top();
            }
            document.body.style.cursor = 'ns-resize';
            document.body.style.userSelect = 'none';
            document.addEventListener('mousemove', onMove, true);
            document.addEventListener('mouseup', onUp, true);
            e.preventDefault();
            e.stopPropagation();
        });
    }
});

// ─── Transcript helpers ───────────────────────────────────────────────────────
function renderHistory(hist) {
    const histEl = document.getElementById('transcript-history');
    if (!histEl) return;
    histEl.innerHTML = '';
    (hist || []).slice(0, -1).forEach(turn => {
        if (!turn.user && !turn.luna) return;
        const row = document.createElement('div');
        row.className = 'history-turn';
        if (turn.user) {
            const u = document.createElement('div');
            u.className = 'history-user';
            u.innerText = 'You: ' + turn.user;
            row.appendChild(u);
        }
        if (turn.luna) {
            const l = document.createElement('div');
            l.className = 'history-luna';
            l.innerText = turn.luna;
            row.appendChild(l);
        }
        histEl.appendChild(row);
    });
}

// ─── Python → JS bridges ─────────────────────────────────────────────────────

window.external_set_transcript = function(userText, lunaReply) {
    const userEl  = document.getElementById('transcript-user-current');
    const lunaEl  = document.getElementById('transcript-luna-current');
    const panel   = document.getElementById('transcript-panel');

    if (userEl) userEl.innerText = userText ? 'You: ' + userText : '';

    if (lunaReply === '') {
        if (lunaEl) lunaEl.innerText = '';
        window._currentTurn = { user: userText, luna: '' };
    } else {
        if (lunaEl) lunaEl.innerText = lunaReply;
        const hist = window._conversationHistory || [];
        const user = window._currentTurn ? window._currentTurn.user : userText;
        hist.push({ user, luna: lunaReply });
        if (hist.length > 4) hist.shift();
        window._conversationHistory = hist;
        window._currentTurn = null;
        renderHistory(hist);
    }
    if (panel) panel.scrollTop = panel.scrollHeight;
};

window.external_append_token = function(token) {
    const lunaEl = document.getElementById('transcript-luna-current');
    if (lunaEl) lunaEl.innerText += (lunaEl.innerText ? ' ' : '') + token.trim();
    const panel = document.getElementById('transcript-panel');
    if (panel) panel.scrollTop = panel.scrollHeight;
};

window.external_set_level = function(level) {
    const normalized = Math.min(level / 0.06, 1.0);
    const scale = 1.0 + normalized * 0.6;
    document.documentElement.style.setProperty('--mic-level', scale.toFixed(3));
};

window.external_show_plan = function(name, content) {
    const panel = document.getElementById('plans-panel');
    if (!panel) return;
    panel.classList.remove('hidden');
    document.getElementById('plans-list-view').classList.add('hidden');
    document.getElementById('plan-editor-view').classList.remove('hidden');
    document.getElementById('btn-back-plans').classList.remove('hidden');
    document.getElementById('plans-title').innerText = name.replace(/-/g, ' ');
    document.getElementById('plan-editor-textarea').value = content;
    document.getElementById('plan-preview').innerHTML = renderMarkdown(content);
    // Reset to preview tab
    document.querySelectorAll('.plan-tab[data-tab]').forEach(b => b.classList.remove('active'));
    const previewTab = document.querySelector('.plan-tab[data-tab="preview"]');
    if (previewTab) previewTab.classList.add('active');
    document.getElementById('plan-tab-preview').classList.remove('hidden');
    document.getElementById('plan-tab-edit').classList.add('hidden');
    window._currentPlanName = name;
};

window.external_set_state = function(stateStr) {
    const container = document.getElementById('visualizer-container');
    const btnMic    = document.getElementById('btn-mic');
    const statusH2  = document.querySelector('#status-text h2');
    const statusP   = document.querySelector('#status-text p');

    container.classList.remove('idle', 'waking', 'listening', 'thinking', 'speaking');
    container.classList.add(stateStr);

    if (stateStr === 'idle') {
        statusH2.innerText = 'Luna is sleeping...';
        statusP.innerText  = 'Click the mic or type below';
        btnMic.classList.remove('active');
        document.documentElement.style.setProperty('--mic-level', '0');
    } else if (stateStr === 'waking') {
        statusH2.innerText = 'Luna is waking...';
        statusP.innerText  = 'Just a moment';
        btnMic.classList.add('active');
    } else if (stateStr === 'listening') {
        statusH2.innerText = 'Luna is listening...';
        statusP.innerText  = 'Ask me anything.';
        btnMic.classList.add('active');
    } else if (stateStr === 'thinking') {
        statusH2.innerText = 'Luna is thinking...';
        statusP.innerText  = 'Processing your message';
        btnMic.classList.remove('active');
        document.documentElement.style.setProperty('--mic-level', '0');
        const lunaEl = document.getElementById('transcript-luna-current');
        if (lunaEl) lunaEl.innerText = '';
    } else if (stateStr === 'speaking') {
        statusH2.innerText = 'Luna is answering...';
        statusP.innerText  = '';
        btnMic.classList.remove('active');
    }
};
