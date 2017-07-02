// Adapted from https://github.com/uid/realtime-turk

PING_INTERVAL = 1000;
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
	Retainer.requestData.active_task = '';
	Retainer.ping();
	Retainer.checkForWork();
	Retainer.finished = false;
	Retainer.error = false;
	Retainer.alertNeeded = true;
	Retainer.numFails = 0;
    },

    ping: function(){
	if (Retainer.requestData.ping_type == 'working'
	    && Retainer.requestData.active_task == '') {
	    // This shouldn't happen -- just wait for the next ping
	    console.log("error: no active_task, but retainer is working");
	    setTimeout(Retainer.ping, PING_INTERVAL);
	}
	$.post(PING_ENDPOINT,
	       Retainer.requestData,
	       function(data, status){
		   console.log('pong', data);
		   $('#waitTime').text(data.wait_time.toFixed(2));
		   var waitPayment = data.wait_time * data.waiting_rate / 60;
		   $('#waitPayment').text(waitPayment.toFixed(2));
		   $('#tasksCompleted').text(data.tasks_completed)
		   var taskPayment = data.tasks_completed * data.per_task_rate;
		   $('#taskPayment').text(taskPayment.toFixed(2));
		   if (data.pool_status == 'finished' || data.terminate_worker) {
		       Retainer.finished = true;
		   }
		   if (data.terminate_work) {
		       alert("Your work on this task is no longer needed. "
			     + "You will still be paid for this task, but "
			     + "please press 'ok' to check for more tasks.");
		       Retainer.switchTasks();
		   }
	       })
	.fail(function(){
		Retainer.numFails++;
		if (Retainer.numFails > 10) {
		    Retainer.error = True;
		}
	    })
	.always(function(){
	    if (Retainer.requestData.ping_type == 'starting') {
		Retainer.requestData.ping_type = 'waiting';
	    }
	    setTimeout(Retainer.ping, PING_INTERVAL);
	});
    },

    checkForWork: function(){
	$('#waitingDiv').show();
	$('#taskFrame').hide();
	if (Retainer.finished) {
	    alert("All of the work for this retainer pool has been completed!"
		  + " Please press 'ok' to submit this HIT and exit the pool."
		  + " Don't worry--the HIT will not be rejected if you haven't"
		  + " yet completed the required number of tasks.");
	    var data = prepare_submit_data();
	    submit_to_frontend(data);
	    return;
	} else if (Retainer.error) {
	    alert("There has been an error communicating with the server."
		  + " Please press 'ok' to submit this HIT and exit the pool."
		  + " Don't worry--this HIT will not be rejected. If you are worried"
		  + " that this error will impact your bonus payment, please take a"
		  + " screenshot of the interface and contact the requester.");
	    var data = prepare_submit_data();
	    submit_to_frontend(data);
	    return;
	}
	$.get(WORK_ENDPOINT,
	      Retainer.requestData,
	      function(data, status){
		  if(data.start === true){
		      Retainer.requestData.ping_type = 'working';
		      Retainer.hasWork(data, Retainer.alertNeeded);
		  }
		  else {
		      Retainer.requestData.active_task = '';
		      if (data.pool_status == 'finished') {
			  Retainer.finished = true;
		      }
		  }
		  console.log(data);
	      },
	      'json'
	     )
	.fail(function(){
		Retainer.numFails++;
		if (Retainer.numFails > 10) {
		    Retainer.error = True;
		}
	    })
	.always(function(){
	    Retainer.alertNeeded = true;
	    if (Retainer.requestData.ping_type == 'waiting' || Retainer.requestData.ping_type == 'starting') {
		setTimeout(Retainer.checkForWork, WORK_INTERVAL);

	    }
	});
    },

    switchTasks: function() {
	Retainer.requestData.ping_type = 'starting';
	Retainer.alertNeeded = false;
	Retainer.checkForWork();
    },

    hasWork: function(data, show_alert){
	Retainer.requestData.active_task = data.task_id
	var task_frame = $('#taskFrame');
	task_frame.attr('src', data.task_url);
	task_frame.load(function() {
	    task_frame.show();
	    task_frame.height(task_frame.contents().height());
	    $('#waitingDiv').hide();

	    // sneakily override the submit behavior of the iframe
	    task_frame[0].contentWindow.submit_to_frontend = Retainer.switchTasks
	});
	if (show_alert)
	    alert('New work is available! Please start working now.');

    }
}
