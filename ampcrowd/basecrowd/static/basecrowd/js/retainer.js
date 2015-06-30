// Adapted from https://github.com/uid/realtime-turk

PING_INTERVAL = 2500;
WORK_INTERVAL = 1000;
PING_ENDPOINT = null;
WORK_ENDPOINT = null;

var Retainer = {
    requestData: null,

    init: function(ping_url, work_url){
	PING_ENDPOINT = ping_url;
	WORK_ENDPOINT = work_url;
	Retainer.requestData = prepare_submit_data();
	Retainer.requestData.ping_type = 'starting';
	Retainer.ping(Retainer.requestData);
	Retainer.checkForWork(Retainer.requestData);
	Retainer.finished = false;
    },

    ping: function(requestData){
	$.post(PING_ENDPOINT,
	       requestData,
	       function(data, status){
		   console.log('pong', data);
		   $('#waitTime').text(data.wait_time.toFixed(2));
		   var waitPayment = data.wait_time * data.waiting_rate / 60;
		   $('#waitPayment').text(waitPayment.toFixed(2));
		   $('#tasksCompleted').text(data.tasks_completed)
		   var taskPayment = data.tasks_completed * data.per_task_rate;
		   $('#taskPayment').text(taskPayment.toFixed(2));
		   if (data.pool_status == 'finished') {
		       Retainer.finished = true;
		   }
	       })
	.always(function(){
	    if (Retainer.requestData.ping_type == 'starting') {
		Retainer.requestData.ping_type = 'waiting';
	    }
	    setTimeout(Retainer.ping, PING_INTERVAL, requestData);
	});
    },

    checkForWork: function(requestData){
	$('#waitingDiv').show();
	$('#taskFrame').hide();
	if (Retainer.finished) {
	    alert("The required work for this retainer pool has been completed!"
		  + " Please press 'ok' to submit this HIT and exit the pool.");
	    var data = prepare_submit_data();
	    submit_to_frontend(data);
	    return;
	}
	$.get(WORK_ENDPOINT,
	      requestData,
	      function(data, status){
		  if(data.start === true){
		      Retainer.requestData.ping_type = 'working';
		      Retainer.hasWork(data);
		  }
		  console.log(data);
	      },
	      'json'
	     )
	.always(function(){
	    if (Retainer.requestData.ping_type == 'waiting' || Retainer.requestData.ping_type == 'starting') {
		setTimeout(Retainer.checkForWork, WORK_INTERVAL, requestData);
	    }
	});
    },

    hasWork: function(data){
	console.log('initialize task here');
	alert('New work is available! Please start working now.');

	var task_frame = $('#taskFrame');
	task_frame.attr('src', data.task_url);
	task_frame.load(function() {
	    task_frame.show();
	    task_frame.height(task_frame.contents().height());
	    $('#waitingDiv').hide();

	    // sneakily override the submit behavior of the iframe
	    task_frame[0].contentWindow.submit_to_frontend = function() {
		Retainer.requestData.ping_type = 'starting';
		Retainer.checkForWork(Retainer.requestData);
	    }
	});

    }
}
