var html = marked.parse(bot_message_text);
$('#chat-area').append('<div class="bot-msg">' + html + '</div>');

$('#send-button').click(function(){
    var message = $('#user-input').val();
    // POST to Rasa HTTP API
    $.ajax({
        url: "http://localhost:5005/webhooks/rest/webhook",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ sender: "user", message: message }),
        success: function(res) {
			for (var i = 0; i < res.length; i++) {
				if (res[i].text) {
					// Use marked.js to convert markdown to HTML before displaying
					var html = marked.parse(bot_message_text);
					$("#chat-area").append('<div class="bot-msg">' + html + '</div>');
				}
			}
		}

    });
});
