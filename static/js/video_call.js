/*
 * 音视频问诊（WebRTC + HTTP 轮询信令）
 *
 * 课设单机部署不引入 WebSocket / Redis：SDP Offer/Answer 与 ICE Candidate 经
 * 后端信令接口（按问诊会话隔离）轮询交换，媒体流仍走浏览器 WebRTC P2P。
 *
 * 双模式：
 *  - video：本地/远端收发摄像头 + 麦克风（画中画）；
 *  - audio：仅收发麦克风，弹窗隐藏画面，保留音频播放。
 *
 * 弹窗容器需通过 data 属性提供：
 *  - data-signal-url：信令接口地址（GET 轮询 / POST 上报）；
 *  - data-me：当前登录角色，patient 或 doctor（后端据此固定身份，前端不可伪造）；
 *  - data-peer：对端显示名（用于弹窗标题）。
 */

(function () {
    'use strict';

    var mask = document.getElementById('videoMask');
    if (!mask) return;

    var panels = document.getElementById('videoPanels');
    var localVideo = document.getElementById('localVideo');
    var remoteVideo = document.getElementById('remoteVideo');
    var statusEl = document.getElementById('videoStatus');
    var openVideoBtn = document.getElementById('openVideo');
    var openVoiceBtn = document.getElementById('openVoice');
    var closeBtn = document.getElementById('closeVideo');
    var startBtn = document.getElementById('startVideo');
    var hangupBtn = document.getElementById('hangupVideo');
    var titleEl = mask.querySelector('.video-modal-head span');

    var signalUrl = mask.getAttribute('data-signal-url');
    var me = mask.getAttribute('data-me');
    var peerLabel = mask.getAttribute('data-peer') || '';
    if (!signalUrl || !me) return;

    var RING_TIMEOUT = 30000; // 主叫等待接通超时：30 秒
    var RTC_CONFIG = {
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
    };

    var localStream = null;
    var peerConn = null;
    var state = 'idle'; // idle | incoming | calling | connected
    var mode = 'video'; // video | audio
    var incomingOffer = null;
    var ringTimer = null;
    var pollTimer = null;
    var pendingIce = [];   // 远端 ICE：pc 未就绪时先缓存
    var processedIce = 0;  // 已处理的远端 ICE 游标

    function csrfToken() {
        var m = document.cookie.match(/csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    function setStatus(text) {
        if (statusEl) {
            statusEl.textContent = text;
            statusEl.classList.remove('hidden');
        }
    }

    function clearRingTimer() {
        if (ringTimer) { clearInterval(ringTimer); ringTimer = null; }
    }

    function startRingTimer(label) {
        var remain = Math.round(RING_TIMEOUT / 1000);
        setStatus(label + '… ' + remain + 's');
        ringTimer = setInterval(function () {
            remain -= 1;
            if (remain <= 0) { hangup('timeout'); return; }
            setStatus(label + '… ' + remain + 's');
        }, 1000);
    }

    function mediaConstraints() {
        return mode === 'audio'
            ? { video: false, audio: true }
            : { video: true, audio: true };
    }

    function applyModeUi() {
        var audio = mode === 'audio';
        if (panels) panels.classList.toggle('is-audio', audio);
        if (titleEl) titleEl.textContent = (audio ? '语音问诊' : '视频问诊') + ' · ' + peerLabel;
        if (startBtn) {
            startBtn.textContent = state === 'incoming' ? '接听' : '发起呼叫';
        }
    }

    function stopLocalStream() {
        if (localStream) {
            localStream.getTracks().forEach(function (t) { t.stop(); });
            localStream = null;
        }
        if (localVideo) localVideo.srcObject = null;
        if (remoteVideo) remoteVideo.srcObject = null;
        if (panels) panels.classList.remove('has-local');
    }

    function postSignal(payload) {
        return fetch(signalUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken(),
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify(payload)
        }).catch(function () { /* 网络抖动由下一轮轮询自愈 */ });
    }

    function createPeerConnection() {
        var pc = new RTCPeerConnection(RTC_CONFIG);

        pc.onicecandidate = function (event) {
            if (event.candidate) {
                postSignal({ type: 'ice', candidate: event.candidate.toJSON() });
            }
        };

        pc.ontrack = function (event) {
            if (remoteVideo) remoteVideo.srcObject = event.streams[0];
            if (state !== 'connected') {
                state = 'connected';
                clearRingTimer();
                setStatus('通话中');
            }
        };

        pc.oniceconnectionstatechange = function () {
            if (!pc) return;
            if (pc.iceConnectionState === 'failed') {
                setStatus('打洞失败：跨网络可能需要 TURN 中继');
            } else if (pc.iceConnectionState === 'disconnected') {
                setStatus('连接已断开');
            }
        };
        return pc;
    }

    function addRemoteIce(candidates) {
        if (!peerConn) {
            pendingIce = pendingIce.concat(candidates);
            return;
        }
        candidates.forEach(function (c) {
            peerConn.addIceCandidate(new RTCIceCandidate(c)).catch(function () {});
        });
    }

    function resetCall() {
        stopLocalStream();
        if (peerConn) { peerConn.close(); peerConn = null; }
        clearRingTimer();
        state = 'idle';
        incomingOffer = null;
        pendingIce = [];
        processedIce = 0;
        if (startBtn) startBtn.disabled = false;
    }

    function openModal(openMode) {
        resetCall();
        mode = openMode || mode;
        applyModeUi();
        setStatus('等待发起呼叫');
        mask.hidden = false;
    }

    // 主叫：建立房间 → 本地采集 → 发 Offer
    async function startCall() {
        if (state !== 'idle') return;
        try {
            localStream = await navigator.mediaDevices.getUserMedia(mediaConstraints());
            if (localVideo) localVideo.srcObject = localStream;
            if (mode === 'video' && panels) panels.classList.add('has-local');

            peerConn = createPeerConnection();
            localStream.getTracks().forEach(function (track) {
                peerConn.addTrack(track, localStream);
            });

            await postSignal({ type: 'call', mode: mode });
            var offer = await peerConn.createOffer();
            await peerConn.setLocalDescription(offer);
            await postSignal({ type: 'offer', mode: mode, sdp: offer.sdp });

            state = 'calling';
            if (startBtn) startBtn.disabled = true;
            startRingTimer('呼叫中');
        } catch (err) {
            alert('无法开启摄像头/麦克风：' + err.message);
            stopLocalStream();
        }
    }

    // 被叫：接受来电 → 回 Answer
    async function acceptCall(data) {
        var offer = data.offer;
        if (!offer) return;
        mode = data.mode || mode;
        state = 'ringing';
        applyModeUi();
        setStatus('接听中…');
        try {
            localStream = await navigator.mediaDevices.getUserMedia(mediaConstraints());
            if (localVideo) localVideo.srcObject = localStream;
            if (mode === 'video' && panels) panels.classList.add('has-local');

            peerConn = createPeerConnection();
            localStream.getTracks().forEach(function (track) {
                peerConn.addTrack(track, localStream);
            });

            await peerConn.setRemoteDescription(new RTCSessionDescription(offer.sdp));
            var cached = pendingIce; pendingIce = [];
            cached.forEach(function (c) {
                peerConn.addIceCandidate(new RTCIceCandidate(c)).catch(function () {});
            });

            var answer = await peerConn.createAnswer();
            await peerConn.setLocalDescription(answer);
            await postSignal({ type: 'answer', sdp: answer.sdp });
            if (startBtn) startBtn.disabled = true;
        } catch (err) {
            setStatus('无法开启麦克风/摄像头：' + err.message);
        }
    }

    function onIncoming(data) {
        incomingOffer = data;
        state = 'incoming';
        mode = data.mode || mode;
        applyModeUi();
        setStatus('对方呼叫中…');
        mask.hidden = false;
        if (startBtn) startBtn.disabled = false;
    }

    function pollRoom() {
        fetch(signalUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data) { schedule(3000); return; }

                if (data.status === 'ended') { hangup('remote'); return; }

                // 被叫：发现对端 Offer，进入来电态
                if (data.offer && state === 'idle') {
                    onIncoming(data);
                }
                // 主叫：收到 Answer
                if (data.answer && peerConn && peerConn.signalingState !== 'stable') {
                    peerConn.setRemoteDescription(
                        new RTCSessionDescription(data.answer.sdp)).catch(function () {});
                }
                // ICE 增量交换
                if (Array.isArray(data.ice) && data.ice.length > processedIce) {
                    var fresh = data.ice.slice(processedIce);
                    processedIce = data.ice.length;
                    addRemoteIce(fresh);
                }

                schedule(state === 'connected' ? 3000 : 1500);
            })
            .catch(function () { schedule(3000); });
    }

    function schedule(delay) {
        pollTimer = setTimeout(pollRoom, delay);
    }

    // reason: timeout 主叫超时；remote 对端挂断；其余为本地主动挂断
    function hangup(reason) {
        postSignal({ type: 'hangup' });
        resetCall();
        setStatus('等待发起呼叫');
        mask.hidden = true;
        if (reason === 'timeout') {
            alert('呼叫超时，对方未接通，已自动挂断。');
        } else if (reason === 'remote') {
            alert('对方已挂断。');
        }
    }

    if (openVideoBtn) openVideoBtn.addEventListener('click', function () { openModal('video'); });
    if (openVoiceBtn) openVoiceBtn.addEventListener('click', function () { openModal('audio'); });
    if (closeBtn) closeBtn.addEventListener('click', function () { hangup(); });
    if (hangupBtn) hangupBtn.addEventListener('click', function () { hangup(); });
    if (startBtn) startBtn.addEventListener('click', function () {
        if (incomingOffer) { acceptCall(incomingOffer); } else { startCall(); }
    });
    mask.addEventListener('click', function (event) {
        if (event.target === mask) hangup();
    });

    // 常驻轮询：弹窗关闭也监听来电，被叫可自动收到呼叫提示
    schedule(1200);
})();
