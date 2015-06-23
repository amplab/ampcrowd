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
	Retainer.requestData.ping_type = 'waiting';
	Retainer.ping(Retainer.requestData);
	Retainer.checkForWork(Retainer.requestData);
    },

    ping: function(requestData){
	$.post(PING_ENDPOINT,
	       requestData,
	       function(data, status){
		   console.log('pong', data);
		   $('#totalWaitTime').text(data.wait_time_total)
		   $('#sessionWaitTime').text(data.wait_time_session)
	       })
	.always(function(){
	   setTimeout(Retainer.ping, PING_INTERVAL, requestData);
	});
    },

    checkForWork: function(requestData){
	$('#waitingDiv').show();
	$('#taskFrame').hide();
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
	    if (Retainer.requestData.ping_type == 'waiting') {
		setTimeout(Retainer.checkForWork, WORK_INTERVAL, requestData);
	    }
	});
    },

    hasWork: function(data){
	console.log('initialize task here');
	alert('start now');

	var task_frame = $('#taskFrame');
	task_frame.attr('src', data.task_url);
	task_frame.load(function() {
	    task_frame.show();
	    task_frame.height(task_frame.contents().height());
	    $('#waitingDiv').hide();

	    // sneakily override the submit behavior of the iframe
	    task_frame[0].contentWindow.submit_to_frontend = function() {
		Retainer.requestData.ping_type = 'waiting';
		Retainer.checkForWork(Retainer.requestData);
	    }
	});

    }
}
