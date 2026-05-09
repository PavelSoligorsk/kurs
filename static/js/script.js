// static/js/script.js - исправленная версия

const API_BASE = 'https://kurs-production-19b6.up.railway.app/api';
let userToken = localStorage.getItem('userToken');
let userRole = localStorage.getItem('userRole');

// Функция для установки cookie
function setCookie(name, value, days) {
    const expires = new Date();
    expires.setTime(expires.getTime() + (days * 24 * 60 * 60 * 1000));
    document.cookie = `${name}=${value};expires=${expires.toUTCString()};path=/`;
}

// Функция для удаления cookie
function deleteCookie(name) {
    document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;`;
}

// Функция для запросов с авторизацией
async function authFetch(url, options = {}) {
    const token = localStorage.getItem('userToken');
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };
    
    if (token && token !== 'admin_token') {
        headers['Authorization'] = `Bearer ${token}`;
    }
    
    const response = await fetch(url, {
        ...options,
        headers: headers,
        credentials: 'include'  // Важно для cookies
    });
    
    // Если 401 - выходим
    if (response.status === 401) {
        logout();
    }
    
    return response;
}

// ========== Управление вкладками ==========
function switchAuthTab(tab) {
    const loginContainer = document.getElementById('loginContainer');
    const registerContainer = document.getElementById('registerContainer');
    const tabs = document.querySelectorAll('.tab-btn');
    
    if (tab === 'login') {
        loginContainer.classList.add('active');
        registerContainer.classList.remove('active');
        tabs[0].classList.add('active');
        tabs[1].classList.remove('active');
    } else {
        loginContainer.classList.remove('active');
        registerContainer.classList.add('active');
        tabs[0].classList.remove('active');
        tabs[1].classList.add('active');
    }
}

function showMainApp() {
    document.getElementById('authModal').style.display = 'none';
    document.getElementById('mainApp').classList.add('show');
    
    if (userRole === 'admin') {
        document.getElementById('userInterface').classList.add('hidden');
        document.getElementById('adminInterface').classList.remove('hidden');
        document.getElementById('userRoleBadge').innerHTML = '<span class="admin-badge">👑 Администратор</span>';
        document.getElementById('userNameDisplay').innerHTML = 'admin';
        loadPendingTickets();
        loadAllUsers();
    } else {
        document.getElementById('userInterface').classList.remove('hidden');
        document.getElementById('adminInterface').classList.add('hidden');
        document.getElementById('userRoleBadge').innerHTML = '<span style="background: #667eea; padding: 5px 15px; border-radius: 20px; color: white;">👤 Пользователь</span>';
        getUserInfo();
    }
}

// ========== Авторизация ==========
document.getElementById('authLoginForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const responseDiv = document.getElementById('authLoginResponse');
    responseDiv.className = 'response show';
    responseDiv.innerHTML = '⏳ Проверка...';
    
    const email = document.getElementById('authEmail').value;
    const password = document.getElementById('authPassword').value;
    
    // Проверка админа
    if (email === 'admin' && password === 'admin123') {
        userRole = 'admin';
        userToken = 'admin_token';
        localStorage.setItem('userRole', 'admin');
        localStorage.setItem('userToken', 'admin_token');
        responseDiv.className = 'response show success';
        responseDiv.innerHTML = '✅ Добро пожаловать, Администратор!';
        setTimeout(() => { showMainApp(); updateFreeSpots(); }, 1000);
        return;
    }
    
    // Проверка пользователя
    try {
        const response = await fetch(`${API_BASE}/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password })
        });
        
        const data = await response.json();
        if (response.ok) {
            userRole = 'user';
            userToken = data.token;
            localStorage.setItem('userRole', 'user');
            localStorage.setItem('userToken', userToken);
            
            // Устанавливаем токен в cookie для бэкенда
            setCookie('token', userToken, 1);
            
            responseDiv.className = 'response show success';
            responseDiv.innerHTML = '✅ Вход выполнен!';
            setTimeout(() => { showMainApp(); updateFreeSpots(); }, 1000);
        } else {
            responseDiv.className = 'response show error';
            responseDiv.innerHTML = `❌ ${data.detail}`;
        }
    } catch (error) {
        responseDiv.className = 'response show error';
        responseDiv.innerHTML = `❌ Ошибка: ${error.message}`;
    }
});

// Регистрация
document.getElementById('authTicketForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const responseDiv = document.getElementById('authTicketResponse');
    responseDiv.className = 'response show';
    responseDiv.innerHTML = '⏳ Отправка...';
    
    const data = {
        email: document.getElementById('regEmail').value,
        "Номер автономии": document.getElementById('regCar').value,
        "Номер паспорта": document.getElementById('regPassport').value,
        password: document.getElementById('regPassword').value
    };
    
    try {
        const response = await fetch(`${API_BASE}/request-ticket`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        
        const result = await response.json();
        if (response.ok) {
            responseDiv.className = 'response show success';
            responseDiv.innerHTML = `✅ ${result.message}<br>📋 ID: ${result.ticket_id}<br>Ожидайте подтверждения.`;
            document.getElementById('authTicketForm').reset();
            setTimeout(() => switchAuthTab('login'), 3000);
        } else {
            responseDiv.className = 'response show error';
            responseDiv.innerHTML = `❌ ${result.detail}`;
        }
    } catch (error) {
        responseDiv.className = 'response show error';
        responseDiv.innerHTML = `❌ Ошибка: ${error.message}`;
    }
});

// ========== Пользовательские функции ==========
async function getUserInfo() {
    if (!userToken || userRole !== 'user') return;
    
    try {
        const response = await authFetch(`${API_BASE}/me`, {
            method: 'GET'
        });
        
        if (response.ok) {
            const user = await response.json();
            document.getElementById('userNameDisplay').innerHTML = `${user.email} ${user.is_inside ? '🚗' : '🏠'}`;
            document.getElementById('carStatus').innerHTML = user.is_inside ? 
                '✅ Ваш автомобиль находится на парковке' : 
                '❌ Ваш автомобиль не на парковке';
        }
    } catch (error) {
        console.error('Error:', error);
    }
}

window.gateEnter = async () => {
    const responseDiv = document.getElementById('gateResponse');
    responseDiv.className = 'response show';
    responseDiv.innerHTML = '⏳ Открытие...';
    
    try {
        const response = await authFetch(`${API_BASE}/gate/enter`, {
            method: 'POST'
        });
        
        const result = await response.json();
        responseDiv.className = `response show ${response.ok ? 'success' : 'error'}`;
        responseDiv.innerHTML = response.ok ? `✅ ${result.message}` : `❌ ${result.detail}`;
        if (response.ok) { updateFreeSpots(); getUserInfo(); }
    } catch (error) {
        responseDiv.className = 'response show error';
        responseDiv.innerHTML = `❌ Ошибка: ${error.message}`;
    }
};

window.gateExit = async () => {
    const responseDiv = document.getElementById('gateResponse');
    responseDiv.className = 'response show';
    responseDiv.innerHTML = '⏳ Открытие...';
    
    try {
        const response = await authFetch(`${API_BASE}/gate/exit`, {
            method: 'POST'
        });
        
        const result = await response.json();
        responseDiv.className = `response show ${response.ok ? 'success' : 'error'}`;
        responseDiv.innerHTML = response.ok ? `✅ ${result.message}` : `❌ ${result.detail}`;
        if (response.ok) { updateFreeSpots(); getUserInfo(); }
    } catch (error) {
        responseDiv.className = 'response show error';
        responseDiv.innerHTML = `❌ Ошибка: ${error.message}`;
    }
};

// ========== Админские функции ==========
window.loadPendingTickets = async () => {
    const ticketsDiv = document.getElementById('adminTicketsList');
    ticketsDiv.innerHTML = '<div style="text-align: center;">⏳ Загрузка...</div>';
    
    try {
        const response = await fetch(`${API_BASE}/admin/pending-tickets`, {
            headers: { 'Authorization': 'Basic ' + btoa('admin:admin123') }
        });
        
        if (!response.ok) throw new Error('Ошибка загрузки');
        
        const tickets = await response.json();
        
        if (tickets.length === 0) {
            ticketsDiv.innerHTML = '<div style="text-align: center; padding: 20px;">📭 Нет заявок</div>';
            return;
        }
        
        ticketsDiv.innerHTML = '';
        tickets.forEach(ticket => {
            const div = document.createElement('div');
            div.className = 'ticket-item';
            div.innerHTML = `
                <p><strong>📧 Email:</strong> ${ticket.email}</p>
                <p><strong>🚗 Авто:</strong> ${ticket.car_number}</p>
                <p><strong>🆔 Паспорт:</strong> ${ticket.passport_number}</p>
                <p><strong>📅 Создана:</strong> ${ticket.created_at}</p>
                <div class="ticket-actions">
                    <button class="approve-btn" onclick="approveTicket(${ticket.id})">✅ Одобрить</button>
                    <button class="reject-btn" onclick="rejectTicket(${ticket.id})">❌ Отклонить</button>
                </div>
            `;
            ticketsDiv.appendChild(div);
        });
    } catch (error) {
        ticketsDiv.innerHTML = `<div class="response show error">❌ ${error.message}</div>`;
    }
};

window.loadAllUsers = async () => {
    const usersDiv = document.getElementById('usersList');
    usersDiv.innerHTML = '<div style="text-align: center;">⏳ Загрузка...</div>';
    
    try {
        const response = await fetch(`${API_BASE}/admin/users`, {
            headers: { 'Authorization': 'Basic ' + btoa('admin:admin123') }
        });
        
        if (!response.ok) throw new Error('Ошибка загрузки');
        
        const users = await response.json();
        
        if (users.length === 0) {
            usersDiv.innerHTML = '<div style="text-align: center; padding: 20px;">👥 Нет пользователей</div>';
            return;
        }
        
        let html = `<table>
            <thead>
                <tr><th>ID</th><th>Email</th><th>Авто</th><th>Статус</th><th>На парковке</th><th>Действия</th></tr>
            </thead>
            <tbody>
        `;
        
        users.forEach(user => {
            let statusBadge = user.is_blocked ? 
                `<span style="background:#dc3545;color:white;padding:2px 6px;border-radius:4px;">🔒 Заблокирован</span>` :
                `<span style="background:#28a745;color:white;padding:2px 6px;border-radius:4px;">✅ Активен</span>`;
            
            let insideBadge = user.is_inside ? 
                `<span style="background:#ffc107;padding:2px 6px;border-radius:4px;">🚗 На парковке</span>` :
                `<span style="background:#28a745;color:white;padding:2px 6px;border-radius:4px;">🏠 Вне парковки</span>`;
            
            html += `
                <tr>
                    <td>${user.id}</td>
                    <td>${user.email}</td>
                    <td>${user.car_number}</td>
                    <td>${statusBadge}</td>
                    <td>${insideBadge}</td>
                    <td class="action-buttons" style="display:flex;gap:5px;flex-wrap:wrap;">
                        ${!user.is_blocked ? `<button onclick="blockUser(${user.id})" style="background:#ff6b6b;padding:5px 10px;">🔨 Блок</button>` : `<button onclick="unblockUser(${user.id})" style="background:#28a745;padding:5px 10px;">🔓 Разблок</button>`}
                        <button onclick="resetPassword(${user.id})" style="background:#17a2b8;padding:5px 10px;">🔑 Сброс</button>
                        ${!user.is_inside ? `<button onclick="deleteUser(${user.id})" style="background:#dc3545;padding:5px 10px;">🗑 Удалить</button>` : ''}
                    </td>
                </tr>
            `;
        });
        
        html += `</tbody></table>`;
        usersDiv.innerHTML = html;
    } catch (error) {
        usersDiv.innerHTML = `<div class="response show error">❌ ${error.message}</div>`;
    }
};

window.approveTicket = async (ticketId) => {
    try {
        const response = await fetch(`${API_BASE}/admin/approve-ticket/${ticketId}`, {
            method: 'POST',
            headers: { 'Authorization': 'Basic ' + btoa('admin:admin123') }
        });
        const result = await response.json();
        alert(response.ok ? `✅ ${result.message}` : `❌ ${result.detail}`);
        if (response.ok) { loadPendingTickets(); loadAllUsers(); updateFreeSpots(); }
    } catch (error) { alert(`❌ ${error.message}`); }
};

window.rejectTicket = async (ticketId) => {
    try {
        const response = await fetch(`${API_BASE}/admin/reject-ticket/${ticketId}`, {
            method: 'POST',
            headers: { 'Authorization': 'Basic ' + btoa('admin:admin123') }
        });
        const result = await response.json();
        alert(response.ok ? `✅ ${result.message}` : `❌ ${result.detail}`);
        if (response.ok) loadPendingTickets();
    } catch (error) { alert(`❌ ${error.message}`); }
};

window.blockUser = async (userId) => {
    const days = prompt('На сколько дней заблокировать? (по умолчанию 7)', '7');
    if (!days) return;
    try {
        const response = await fetch(`${API_BASE}/admin/block-user/${userId}?block_days=${days}`, {
            method: 'POST',
            headers: { 'Authorization': 'Basic ' + btoa('admin:admin123') }
        });
        const result = await response.json();
        alert(response.ok ? `✅ ${result.message}` : `❌ ${result.detail}`);
        if (response.ok) loadAllUsers();
    } catch (error) { alert(`❌ ${error.message}`); }
};

window.unblockUser = async (userId) => {
    try {
        const response = await fetch(`${API_BASE}/admin/unblock-user/${userId}`, {
            method: 'POST',
            headers: { 'Authorization': 'Basic ' + btoa('admin:admin123') }
        });
        const result = await response.json();
        alert(response.ok ? `✅ ${result.message}` : `❌ ${result.detail}`);
        if (response.ok) loadAllUsers();
    } catch (error) { alert(`❌ ${error.message}`); }
};

window.deleteUser = async (userId) => {
    if (!confirm('Удалить пользователя? (Машина не должна быть на парковке)')) return;
    try {
        const response = await fetch(`${API_BASE}/admin/delete-user/${userId}`, {
            method: 'DELETE',
            headers: { 'Authorization': 'Basic ' + btoa('admin:admin123') }
        });
        const result = await response.json();
        alert(response.ok ? `✅ ${result.message}` : `❌ ${result.detail}`);
        if (response.ok) { loadAllUsers(); updateFreeSpots(); }
    } catch (error) { alert(`❌ ${error.message}`); }
};

window.resetPassword = async (userId) => {
    const newPass = prompt('Введите новый пароль (минимум 6 символов)', 'newpass123');
    if (!newPass || newPass.length < 6) { alert('Пароль должен быть не менее 6 символов'); return; }
    try {
        const response = await fetch(`${API_BASE}/admin/reset-password/${userId}?new_password=${encodeURIComponent(newPass)}`, {
            method: 'POST',
            headers: { 'Authorization': 'Basic ' + btoa('admin:admin123') }
        });
        const result = await response.json();
        alert(response.ok ? `✅ ${result.message}` : `❌ ${result.detail}`);
    } catch (error) { alert(`❌ ${error.message}`); }
};

// ========== Общие функции ==========
window.logout = () => {
    // Удаляем cookie
    deleteCookie('token');
    
    // Очищаем localStorage
    localStorage.clear();
    userToken = null;
    userRole = null;
    
    // Показываем модальное окно
    document.getElementById('mainApp').classList.remove('show');
    document.getElementById('authModal').style.display = 'flex';
    document.getElementById('authLoginForm').reset();
    document.getElementById('authTicketForm').reset();
    
    // Очищаем ответы
    const responses = ['authLoginResponse', 'authTicketResponse', 'gateResponse'];
    responses.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.className = 'response';
    });
};

async function updateFreeSpots() {
    try {
        const response = await fetch(`${API_BASE}/free-spots`);
        const data = await response.json();
        document.getElementById('freeSpots').textContent = data.free_spots;
    } catch (error) { console.error(error); }
}

function checkSavedSession() {
    const savedRole = localStorage.getItem('userRole');
    const savedToken = localStorage.getItem('userToken');
    
    if (savedRole && savedToken) {
        userRole = savedRole;
        userToken = savedToken;
        
        // Восстанавливаем cookie для пользователей
        if (savedRole === 'user') {
            setCookie('token', savedToken, 1);
        }
        
        showMainApp();
        updateFreeSpots();
    }
}

// ==================== АДМИН: УПРАВЛЕНИЕ ШЛАГБАУМОМ ====================
window.adminOpenGate = async () => {
    const respDiv = document.getElementById('adminGateResponse');
    respDiv.className = 'response show';
    respDiv.innerHTML = '⏳ Открытие шлагбаума...';
    
    try {
        const response = await fetch(`${API_BASE}/admin/gate/open`, {
            method: 'POST',
            headers: { 'Authorization': 'Basic ' + btoa('admin:admin123') }
        });
        
        const data = await response.json();
        respDiv.className = `response show ${response.ok ? 'success' : 'error'}`;
        respDiv.innerHTML = response.ok ? `✅ ${data.message}` : `❌ ${data.detail}`;
    } catch (error) {
        respDiv.className = 'response show error';
        respDiv.innerHTML = `❌ Ошибка: ${error.message}`;
    }
};

window.adminCloseGate = async () => {
    const respDiv = document.getElementById('adminGateResponse');
    respDiv.className = 'response show';
    respDiv.innerHTML = '⏳ Закрытие шлагбаума...';
    
    try {
        const response = await fetch(`${API_BASE}/admin/gate/close`, {
            method: 'POST',
            headers: { 'Authorization': 'Basic ' + btoa('admin:admin123') }
        });
        
        const data = await response.json();
        respDiv.className = `response show ${response.ok ? 'success' : 'error'}`;
        respDiv.innerHTML = response.ok ? `✅ ${data.message}` : `❌ ${data.detail}`;
    } catch (error) {
        respDiv.className = 'response show error';
        respDiv.innerHTML = `❌ Ошибка: ${error.message}`;
    }
};

// Инициализация
updateFreeSpots();
setInterval(updateFreeSpots, 5000);
checkSavedSession();