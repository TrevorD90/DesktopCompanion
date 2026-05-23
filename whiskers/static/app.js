// Whiskers tutor — browser client
// SSE event consumer + UI controller + sequenced audio player.

(function () {
  'use strict';

  // ---------- state ----------
  const state = {
    sessionId: null,
    speaker: 'Rowan',
    knownSpeakers: ['Rowan'],
    problems: [],
    currentIndex: null,
    audioQueue: [],         // array of {url, seq, text}
    audioPlaying: false,
    audioEl: null,
    settings: null,
    micListening: false,
    errorsCount: 0,
  };

  // ---------- elements ----------
  const $ = id => document.getElementById(id);
  const els = {
    speakerPills: $('speaker-pills'),
    addSpeaker: $('btn-add-speaker'),
    addSpeakerDialog: $('add-speaker-dialog'),
    addSpeakerName: $('add-speaker-name'),
    addSpeakerPronunciation: $('add-speaker-pronunciation'),
    micBtn: $('btn-mic'),
    micStatus: $('mic-status'),

    problemIndicator: $('problem-indicator'),
    problemBody: $('problem-body'),
    problemList: $('problem-list'),
    prev: $('btn-prev'),
    next: $('btn-next'),

    fileInput: $('file-input'),
    pasteText: $('paste-text'),
    pasteSubmit: $('btn-paste-submit'),

    transcript: $('transcript'),
    voiceIndicator: $('voice-indicator'),
    messageForm: $('message-form'),
    messageInput: $('message-input'),

    settingsApiKey: $('settings-api-key'),
    settingsApiKeyStatus: $('settings-api-key-status'),
    settingsVoice: $('settings-voice'),
    settingsSensitivity: $('settings-sensitivity'),
    settingsSensitivityValue: $('settings-sensitivity-value'),
    pronunciationsList: $('pronunciations-list'),
    saveSettings: $('btn-save-settings'),
    settingsSaved: $('settings-saved'),
    ttsProvider: $('settings-tts-provider'),
    elKey: $('settings-el-key'),
    elKeyStatus: $('settings-el-key-status'),
    elVoice: $('settings-el-voice'),
    elModel: $('settings-el-model'),
    elRefreshVoices: $('btn-el-refresh-voices'),
    elUsageText: $('el-usage-text'),
    elUsageBarFill: $('el-usage-bar-fill'),
    elUsageReset: $('el-usage-reset'),
    elRefreshUsage: $('btn-el-refresh-usage'),
    errorsBtn: $('btn-errors'),
    errorsCount: $('errors-count'),
    errorsDialog: $('errors-dialog'),
    errorsList: $('errors-list'),
    errorsEmpty: $('errors-empty'),
    errorsClear: $('btn-errors-clear'),
    errorsClose: $('btn-errors-close'),
  };

  // ---------- helpers ----------
  function api(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(r => {
      if (!r.ok) return r.json().then(j => { throw new Error(j.error || ('HTTP ' + r.status)); });
      return r.json();
    });
  }

  function addTurn(role, speaker, text, opts) {
    const div = document.createElement('div');
    div.className = 'turn ' + role;
    const roleSpan = document.createElement('span');
    roleSpan.className = 'role';
    roleSpan.textContent = (speaker || role) + ':';
    const textSpan = document.createElement('span');
    textSpan.className = 'text';
    textSpan.textContent = text;
    div.appendChild(roleSpan);
    div.appendChild(textSpan);
    els.transcript.appendChild(div);
    els.transcript.scrollTop = els.transcript.scrollHeight;
  }

  // ---------- speakers ----------
  function renderSpeakers() {
    els.speakerPills.innerHTML = '';
    state.knownSpeakers.forEach(name => {
      const btn = document.createElement('button');
      btn.textContent = name;
      if (name === state.speaker) btn.classList.add('active');
      btn.addEventListener('click', () => {
        api('POST', '/api/speaker/set', { name }).catch(err => addTurn('error', 'error', err.message));
      });
      els.speakerPills.appendChild(btn);
    });
  }

  els.addSpeaker.addEventListener('click', () => {
    els.addSpeakerName.value = '';
    els.addSpeakerPronunciation.value = '';
    els.addSpeakerDialog.showModal();
  });

  els.addSpeakerDialog.addEventListener('close', () => {
    if (els.addSpeakerDialog.returnValue !== 'confirm') return;
    const name = els.addSpeakerName.value.trim();
    const pronunciation = els.addSpeakerPronunciation.value.trim();
    if (!name) return;
    api('POST', '/api/speaker/add', { name, pronunciation }).then(r => {
      state.knownSpeakers = r.known || state.knownSpeakers;
      renderSpeakers();
      loadSettings();  // refresh pronunciations editor
    }).catch(err => addTurn('error', 'error', err.message));
  });

  // ---------- mic ----------
  els.micBtn.addEventListener('click', () => {
    if (state.micListening) {
      api('POST', '/api/mic/stop');
    } else {
      api('POST', '/api/mic/start');
    }
  });

  function setMicStatus(s) {
    els.micStatus.className = 'mic-status ' + s;
    els.micStatus.textContent = s.replace('_', ' ');
    state.micListening = (s === 'recording' || s === 'wake_detected');
    els.micBtn.textContent = state.micListening ? 'Stop listening' : 'Start listening';
    els.micBtn.classList.toggle('listening', state.micListening);
  }

  // ---------- problems ----------
  function renderProblems(payload) {
    state.problems = payload.problems || [];
    state.currentIndex = payload.current_index;
    els.problemList.innerHTML = '';

    if (!state.problems.length) {
      els.problemIndicator.textContent = 'No coursework loaded';
      els.problemBody.innerHTML = '<p class="muted">Upload a worksheet, take a photo, or paste a question to get started.</p>';
      return;
    }

    state.problems.forEach((p, i) => {
      const li = document.createElement('li');
      li.textContent = `${i + 1}. ${p.preview}`;
      if (i === state.currentIndex) li.classList.add('active');
      li.addEventListener('click', () => {
        api('POST', '/api/problem/set', { index: i }).catch(err => addTurn('error', 'error', err.message));
      });
      els.problemList.appendChild(li);
    });

    const cur = state.problems[state.currentIndex];
    els.problemIndicator.textContent = `Problem ${state.currentIndex + 1} of ${state.problems.length}`;
    if (cur.has_image) {
      els.problemBody.innerHTML = `<img alt="Problem ${state.currentIndex + 1}" src="/api/problem/${cur.id}/image" />`;
    } else {
      els.problemBody.textContent = cur.preview || '(no text)';
    }
  }

  els.prev.addEventListener('click', () => {
    if (state.currentIndex == null) return;
    api('POST', '/api/problem/set', { index: state.currentIndex - 1 });
  });
  els.next.addEventListener('click', () => {
    if (state.currentIndex == null) return;
    api('POST', '/api/problem/set', { index: state.currentIndex + 1 });
  });

  els.fileInput.addEventListener('change', () => {
    const f = els.fileInput.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append('file', f);
    fetch('/api/upload', { method: 'POST', body: fd })
      .then(r => r.json())
      .then(r => {
        if (r.error) { addTurn('error', 'error', r.error); return; }
        renderProblems(r);
      })
      .catch(err => addTurn('error', 'error', err.message));
    els.fileInput.value = '';
  });

  els.pasteSubmit.addEventListener('click', () => {
    const text = els.pasteText.value.trim();
    if (!text) return;
    const fd = new FormData();
    fd.append('text', text);
    fetch('/api/upload', { method: 'POST', body: fd })
      .then(r => r.json())
      .then(r => {
        if (r.error) { addTurn('error', 'error', r.error); return; }
        renderProblems(r);
        els.pasteText.value = '';
      })
      .catch(err => addTurn('error', 'error', err.message));
  });

  // ---------- voice indicator ----------
  function setVoiceIndicator(provider, voiceLabel, isFallback) {
    const el = els.voiceIndicator;
    if (!provider) {
      el.textContent = '—';
      el.className = 'voice-indicator';
      el.title = 'No TTS used yet this session';
      return;
    }
    const label = voiceLabel || '?';
    const providerName = provider === 'elevenlabs' ? 'ElevenLabs'
                       : provider === 'kokoro'     ? 'Kokoro'
                       : provider;
    el.textContent = isFallback
      ? `⚠ ${providerName} · ${label} (fallback)`
      : `${providerName} · ${label}`;
    el.className = 'voice-indicator ' + provider + (isFallback ? ' fallback' : '');
    el.title = isFallback
      ? `Configured provider failed — using ${providerName} fallback. Check Settings.`
      : `Active TTS: ${providerName} (${label})`;
  }

  function configuredVoiceLabel() {
    const provider = (state.settings && state.settings.tts_provider) || 'elevenlabs';
    if (provider === 'elevenlabs') {
      const sel = els.elVoice;
      if (sel && sel.selectedIndex >= 0) {
        const opt = sel.options[sel.selectedIndex];
        // Voice options are rendered as "Name — category"; grab the name half.
        if (opt.value) return opt.textContent.split(' — ')[0];
        // Empty value = "(auto-pick first)" — show the first real voice instead.
        for (let i = 0; i < sel.options.length; i++) {
          if (sel.options[i].value) {
            return sel.options[i].textContent.split(' — ')[0] + ' (auto)';
          }
        }
      }
      return '(no voice loaded — paste key and Refresh)';
    }
    return (state.settings && state.settings.kokoro_voice) || 'af_bella';
  }

  function refreshConfiguredVoiceIndicator() {
    if (!state.settings) return;
    const provider = state.settings.tts_provider || 'elevenlabs';
    setVoiceIndicator(provider, configuredVoiceLabel(), false);
  }

  function voiceLabelFromAudioEvent(provider, voice) {
    if (provider === 'elevenlabs') {
      // voice is a voice_id; look up the human name from the picker options.
      const opts = els.elVoice.options;
      for (let i = 0; i < opts.length; i++) {
        if (opts[i].value === voice) {
          return opts[i].textContent.split(' — ')[0];
        }
      }
      return voice ? voice.slice(0, 8) + '…' : '?';
    }
    return voice || 'system';
  }

  // ---------- audio queue ----------
  function enqueueAudio(url, seq, text) {
    state.audioQueue.push({ url, seq, text });
    state.audioQueue.sort((a, b) => a.seq - b.seq);
    playNextIfIdle();
  }

  function playNextIfIdle() {
    if (state.audioPlaying || state.audioQueue.length === 0) return;
    const next = state.audioQueue.shift();
    state.audioPlaying = true;
    if (!state.audioEl) {
      state.audioEl = new Audio();
      state.audioEl.addEventListener('ended', () => {
        state.audioPlaying = false;
        playNextIfIdle();
      });
      state.audioEl.addEventListener('error', () => {
        state.audioPlaying = false;
        playNextIfIdle();
      });
    }
    state.audioEl.src = next.url;
    state.audioEl.play().catch(err => {
      // Autoplay policy: first play needs user gesture. Show a one-time hint.
      addTurn('system', 'system', '(Audio blocked — click anywhere to enable playback.)');
      state.audioPlaying = false;
      document.body.addEventListener('click', resumeAudio, { once: true });
    });
  }
  function resumeAudio() {
    if (state.audioEl) state.audioEl.play().catch(() => {});
    state.audioPlaying = !!state.audioEl && !state.audioEl.paused;
  }

  // ---------- message form ----------
  els.messageForm.addEventListener('submit', e => {
    e.preventDefault();
    const text = els.messageInput.value.trim();
    if (!text) return;
    els.messageInput.value = '';
    api('POST', '/api/message', { text }).catch(err => addTurn('error', 'error', err.message));
  });

  // ---------- settings ----------
  function renderPronunciations(table) {
    els.pronunciationsList.innerHTML = '';
    state.knownSpeakers.forEach(name => {
      const row = document.createElement('div');
      row.className = 'pron-row';
      const label = document.createElement('span');
      label.className = 'name';
      label.textContent = name;
      const input = document.createElement('input');
      input.type = 'text';
      input.dataset.name = name;
      input.value = (table || {})[name] || '';
      input.placeholder = 'phonetic respelling';
      row.appendChild(label);
      row.appendChild(input);
      els.pronunciationsList.appendChild(row);
    });
  }

  function loadSettings() {
    api('GET', '/api/settings').then(s => {
      state.settings = s;
      els.settingsApiKey.placeholder = s.anthropic_api_key_set ? '(stored — leave blank to keep)' : 'sk-ant-...';
      els.settingsApiKeyStatus.textContent = s.anthropic_api_key_set ? 'key set' : 'no key — required';

      els.ttsProvider.value = s.tts_provider || 'elevenlabs';
      els.elKey.placeholder = s.elevenlabs_api_key_set ? '(stored — leave blank to keep)' : 'sk_...';
      els.elKeyStatus.textContent = s.elevenlabs_api_key_set ? 'key set' : 'no key';

      // Populate model dropdown
      els.elModel.innerHTML = '';
      (s.available_models || []).forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = m.name;
        if (m.id === s.elevenlabs_model) opt.selected = true;
        els.elModel.appendChild(opt);
      });

      // Populate the ElevenLabs voice picker + usage (requires API calls)
      refreshElevenlabsVoices(s.elevenlabs_voice_id || '');
      if (s.elevenlabs_api_key_set) refreshElevenlabsUsage();

      els.settingsVoice.innerHTML = '';
      (s.available_voices || []).forEach(v => {
        const opt = document.createElement('option');
        opt.value = v;
        opt.textContent = v;
        if (v === s.kokoro_voice) opt.selected = true;
        els.settingsVoice.appendChild(opt);
      });

      const sens = parseFloat(s.wake_word_sensitivity) || 0.5;
      els.settingsSensitivity.value = sens;
      els.settingsSensitivityValue.textContent = sens.toFixed(2);

      renderPronunciations(s.name_pronunciations || {});
      refreshConfiguredVoiceIndicator();
    }).catch(err => addTurn('error', 'error', err.message));
  }

  function refreshElevenlabsVoices(selectedId) {
    api('GET', '/api/elevenlabs/voices').then(r => {
      const voices = r.voices || [];
      els.elVoice.innerHTML = '';
      if (!voices.length) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = '(none — paste API key and click Refresh)';
        els.elVoice.appendChild(opt);
      } else {
        const blank = document.createElement('option');
        blank.value = '';
        blank.textContent = '(auto-pick first premade)';
        els.elVoice.appendChild(blank);

        // Split: premade voices work on the free-tier API; library voices
        // (professional/cloned/generated/community) require a paid plan.
        const premade = voices.filter(v => (v.category || '').toLowerCase() === 'premade');
        const library = voices.filter(v => (v.category || '').toLowerCase() !== 'premade');

        if (premade.length) {
          const g = document.createElement('optgroup');
          g.label = 'Premade (free-tier safe)';
          premade.forEach(v => {
            const opt = document.createElement('option');
            opt.value = v.voice_id;
            opt.textContent = v.name;
            if (v.voice_id === selectedId) opt.selected = true;
            g.appendChild(opt);
          });
          els.elVoice.appendChild(g);
        }
        if (library.length) {
          const g = document.createElement('optgroup');
          g.label = 'Voice Library (requires paid plan on API)';
          library.forEach(v => {
            const opt = document.createElement('option');
            opt.value = v.voice_id;
            opt.textContent = v.name + (v.category ? ` — ${v.category}` : '');
            if (v.voice_id === selectedId) opt.selected = true;
            g.appendChild(opt);
          });
          els.elVoice.appendChild(g);
        }
      }
      // Indicator depends on the picker — refresh after we've populated it.
      refreshConfiguredVoiceIndicator();
    }).catch(() => {
      els.elVoice.innerHTML = '<option value="">(failed to load — check key)</option>';
      refreshConfiguredVoiceIndicator();
    });
  }

  els.elRefreshVoices.addEventListener('click', () => {
    // If the user has just pasted a new key, save it first so the server can
    // use it when listing voices.
    const newKey = els.elKey.value.trim();
    const chain = newKey
      ? api('POST', '/api/settings', { elevenlabs_api_key: newKey }).then(() => { els.elKey.value = ''; })
      : Promise.resolve();
    chain.then(() => {
      refreshElevenlabsVoices(els.elVoice.value);
      refreshElevenlabsUsage();
    }).catch(err => addTurn('error', 'error', err.message));
  });

  function refreshElevenlabsUsage() {
    fetch('/api/elevenlabs/usage')
      .then(r => r.json().then(j => ({ status: r.status, j })))
      .then(({ status, j }) => {
        if (status !== 200) {
          els.elUsageText.textContent = `Usage: ${j.error || 'failed'}`;
          els.elUsageBarFill.style.width = '0%';
          els.elUsageBarFill.className = 'usage-bar-fill';
          els.elUsageReset.textContent = j.hint || '';
          return;
        }
        const used = j.used.toLocaleString();
        const limit = j.limit.toLocaleString();
        const pct = j.pct || 0;
        els.elUsageText.textContent = `Usage: ${used} / ${limit} characters this month (${pct}%)`;
        els.elUsageBarFill.style.width = Math.min(100, pct) + '%';
        els.elUsageBarFill.className = 'usage-bar-fill' + (pct >= 90 ? ' crit' : pct >= 70 ? ' warn' : '');
        if (j.next_reset_unix) {
          const d = new Date(j.next_reset_unix * 1000);
          els.elUsageReset.textContent = `Resets ${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
        } else {
          els.elUsageReset.textContent = '';
        }
      })
      .catch(err => {
        els.elUsageText.textContent = 'Usage: ' + err.message;
      });
  }

  els.elRefreshUsage.addEventListener('click', refreshElevenlabsUsage);

  els.settingsSensitivity.addEventListener('input', () => {
    els.settingsSensitivityValue.textContent = parseFloat(els.settingsSensitivity.value).toFixed(2);
  });

  els.saveSettings.addEventListener('click', () => {
    const body = {
      kokoro_voice: els.settingsVoice.value,
      wake_word_sensitivity: parseFloat(els.settingsSensitivity.value),
      tts_provider: els.ttsProvider.value,
      elevenlabs_voice_id: els.elVoice.value,
      elevenlabs_model: els.elModel.value,
    };
    const apiKey = els.settingsApiKey.value.trim();
    if (apiKey) body.anthropic_api_key = apiKey;
    const elKey = els.elKey.value.trim();
    if (elKey) body.elevenlabs_api_key = elKey;

    const pronunciations = {};
    els.pronunciationsList.querySelectorAll('input').forEach(inp => {
      const val = inp.value.trim();
      if (val) pronunciations[inp.dataset.name] = val;
    });
    body.name_pronunciations = pronunciations;

    api('POST', '/api/settings', body).then(() => {
      els.settingsSaved.textContent = 'saved';
      els.settingsApiKey.value = '';
      els.elKey.value = '';
      setTimeout(() => els.settingsSaved.textContent = '', 2000);
      loadSettings();
    }).catch(err => addTurn('error', 'error', err.message));
  });

  // ---------- SSE ----------
  function connectEvents() {
    const es = new EventSource('/events');
    es.onmessage = ev => {
      let data;
      try { data = JSON.parse(ev.data); } catch (_) { return; }
      handleEvent(data);
    };
    es.onerror = () => {
      addTurn('error', 'error', '(disconnected — retrying)');
    };
  }

  function handleEvent(data) {
    switch (data.type) {
      case 'snapshot':
        state.sessionId = data.session_id;
        state.speaker = data.speaker;
        state.knownSpeakers = data.known_speakers || [data.speaker];
        renderSpeakers();
        if (data.problems) renderProblems(data.problems);
        break;
      case 'speaker_changed':
        state.speaker = data.speaker;
        if (data.known) state.knownSpeakers = data.known;
        renderSpeakers();
        addTurn('system', 'system', `(speaker → ${data.speaker})`);
        break;
      case 'problem_changed':
        renderProblems({ problems: data.problems, current_index: data.current_index });
        break;
      case 'mic_status':
        setMicStatus(data.state);
        break;
      case 'transcript':
        addTurn(data.role || 'user', data.speaker || data.role, data.text || '');
        break;
      case 'audio': {
        enqueueAudio(data.url, data.seq, data.text);
        const configured = (state.settings && state.settings.tts_provider) || 'elevenlabs';
        const isFallback = (data.provider && data.provider !== configured);
        setVoiceIndicator(data.provider, voiceLabelFromAudioEvent(data.provider, data.voice), isFallback);
        break;
      }
      case 'audio_error':
        // Synthesis failed for a sentence — already shown in transcript; ignore audio.
        break;
      case 'turn_complete':
        // Server has emitted all sentences for this turn. The mic will
        // auto-re-arm a moment after audio playback finishes.
        break;
      case 'session_paused':
        addTurn('system', 'system', '(paused — click the mic to resume)');
        break;
      case 'session_resumed':
        addTurn('system', 'system', '(listening again)');
        break;
      case 'error':
        addTurn('error', data.source || 'error', data.message || 'unknown error');
        if (typeof data.count === 'number') setErrorsCount(data.count);
        else setErrorsCount(state.errorsCount + 1);
        break;
      case 'errors_cleared':
        setErrorsCount(0);
        break;
      default:
        // ignore
    }
  }

  // ---------- error log ----------
  function setErrorsCount(n) {
    state.errorsCount = Math.max(0, n | 0);
    els.errorsCount.textContent = String(state.errorsCount);
    els.errorsCount.classList.toggle('has-errors', state.errorsCount > 0);
  }

  function formatTimestamp(ts) {
    const d = new Date((ts || 0) * 1000);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleTimeString();
  }

  function renderErrorsList(entries) {
    els.errorsList.innerHTML = '';
    if (!entries || !entries.length) {
      els.errorsEmpty.hidden = false;
      return;
    }
    els.errorsEmpty.hidden = true;
    // Newest first.
    entries.slice().reverse().forEach(e => {
      const li = document.createElement('li');
      li.className = e.level === 'warn' ? 'err-level-warn' : '';
      const head = document.createElement('div');
      head.className = 'err-head';
      const src = document.createElement('span');
      src.className = 'err-source';
      src.textContent = e.source || 'unknown';
      const ts = document.createElement('span');
      ts.className = 'err-ts';
      ts.textContent = formatTimestamp(e.ts);
      head.appendChild(src);
      head.appendChild(ts);
      const msg = document.createElement('div');
      msg.className = 'err-msg';
      msg.textContent = e.message || '';
      li.appendChild(head);
      li.appendChild(msg);
      els.errorsList.appendChild(li);
    });
  }

  function openErrors() {
    api('GET', '/api/errors').then(r => {
      renderErrorsList(r.entries || []);
      setErrorsCount(r.count || 0);
      els.errorsDialog.showModal();
    }).catch(err => addTurn('error', 'error', err.message));
  }

  function loadErrorsCount() {
    api('GET', '/api/errors').then(r => setErrorsCount(r.count || 0)).catch(() => {});
  }

  els.errorsBtn.addEventListener('click', openErrors);
  els.errorsClose.addEventListener('click', e => { e.preventDefault(); els.errorsDialog.close(); });
  els.errorsClear.addEventListener('click', e => {
    e.preventDefault();
    api('POST', '/api/errors/clear').then(() => {
      renderErrorsList([]);
      setErrorsCount(0);
    }).catch(err => addTurn('error', 'error', err.message));
  });

  // Capture client-side errors too so they land in the same log.
  window.addEventListener('error', ev => {
    const msg = (ev.message || 'unknown') + (ev.filename ? ` (${ev.filename}:${ev.lineno})` : '');
    fetch('/api/errors/log', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({source: 'browser', message: msg}),
    }).catch(() => {});
  });
  window.addEventListener('unhandledrejection', ev => {
    const msg = String(ev.reason && (ev.reason.stack || ev.reason.message) || ev.reason || 'unknown rejection');
    fetch('/api/errors/log', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({source: 'browser', message: msg}),
    }).catch(() => {});
  });

  // ---------- boot ----------
  setMicStatus('idle');
  setErrorsCount(0);
  loadErrorsCount();
  loadSettings();
  connectEvents();
})();
