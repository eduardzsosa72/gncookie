<?php
// telegram_bot.php
define('BOT_TOKEN', 'TU_BOT_TOKEN');
define('API_URL', 'https://tu-app.railway.app');

function callAPI($endpoint, $method = 'GET', $data = null) {
    $url = API_URL . $endpoint;
    $ch = curl_init();
    curl_setopt($ch, CURLOPT_URL, $url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    
    if ($method === 'POST') {
        curl_setopt($ch, CURLOPT_POST, true);
        if ($data) {
            curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($data));
            curl_setopt($ch, CURLOPT_HTTPHEADER, ['Content-Type: application/json']);
        }
    }
    
    $response = curl_exec($ch);
    curl_close($ch);
    
    return json_decode($response, true);
}

function sendMessage($chat_id, $text) {
    $url = "https://api.telegram.org/bot" . BOT_TOKEN . "/sendMessage";
    $data = ['chat_id' => $chat_id, 'text' => $text, 'parse_mode' => 'HTML'];
    
    $ch = curl_init();
    curl_setopt($ch, CURLOPT_URL, $url);
    curl_setopt($ch, CURLOPT_POST, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, http_build_query($data));
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_exec($ch);
    curl_close($ch);
}

// Webhook para recibir resultados
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_GET['webhook'])) {
    $data = json_decode(file_get_contents('php://input'), true);
    if ($data && isset($data['callback_id'])) {
        $result = $data['result'];
        if ($data['status'] === 'completed' && $result['success']) {
            $msg = "✅ <b>¡Cuenta creada!</b>\n\n";
            $msg .= "📱 Teléfono: <code>{$result['phone']}</code>\n";
            $msg .= "🔑 Contraseña: <code>{$result['password']}</code>\n";
            $msg .= "👤 Nombre: {$result['name']}";
        } else {
            $msg = "❌ <b>Error:</b>\n<code>" . ($data['error'] ?? 'Unknown error') . "</code>";
        }
        sendMessage($data['callback_id'], $msg);
    }
    http_response_code(200);
    exit;
}

// Procesar comandos de Telegram
$update = json_decode(file_get_contents('php://input'), true);
if (isset($update['message'])) {
    $chat_id = $update['message']['chat']['id'];
    $text = trim($update['message']['text'] ?? '');
    
    switch ($text) {
        case '/start':
            sendMessage($chat_id, "🤖 Bot de creación de cuentas Amazon\n\n/comenzar - Iniciar creación");
            break;
            
        case '/comenzar':
            $response = callAPI('/create', 'POST', ['callback_id' => $chat_id]);
            if ($response && isset($response['task_id'])) {
                sendMessage($chat_id, "🔄 Creando cuenta...\nID: {$response['task_id']}\nUsa /estado {$response['task_id']} para ver progreso");
            } else {
                sendMessage($chat_id, "❌ Error al iniciar");
            }
            break;
            
        default:
            if (strpos($text, '/estado') === 0) {
                $parts = explode(' ', $text);
                $task_id = $parts[1] ?? null;
                if ($task_id) {
                    $status = callAPI("/status/$task_id");
                    if ($status) {
                        $msg = "📊 <b>Estado:</b> {$status['status']}\n";
                        if ($status['status'] === 'completed') {
                            $msg .= "\n✅ Completada";
                        } elseif ($status['status'] === 'failed') {
                            $msg .= "\n❌ Error: {$status['error']}";
                        }
                        sendMessage($chat_id, $msg);
                    }
                }
            }
            break;
    }
}