// Adapted from https://github.com/uid/realtime-turk

var Retainer = {
	aid: null,
	wid: null,
	hid: null,
	ping_type: 'waiting',

	init: function(worker_id, assignment_id, hit_id){
		Retainer.aid = assignment_id
		Retainer.wid = worker_id
		Retainer.hid = hit_id

		Retainer.ping(worker_id, assignment_id, hit_id, Retainer.ping_type)
		Retainer.checkForWork(assignment_id)
	},

	ping: function(worker_id, assignment_id, hit_id, ping_type){
		$.get(PING_ENDPOINT +
			'worker/' + worker_id + '/assignment/' + assignment_id + '/hit/' + hit_id + '/event/' + ping_type, 
			function(data, status){
				console.log('pong', data)
				setTimeout(Retainer.ping, PING_INTERVAL, worker_id, assignment_id, hit_id, Retainer.ping_type)
			}
		)
	},

	checkForWork: function(assignment_id){
		$.ajax({
			url: WORK_ENDPOINT + 'assignment/' + assignment_id,
			success: function(data, status){
				if(data.start === true){
					Retainer.ping_type = 'working'
					Retainer.hasWork(data)
				} else {
					setTimeout(Retainer.checkForWork, WORK_INTERVAL, assignment_id)
				}
				console.log(data)
			},
			dataType: 'json'
		})
	},

	hasWork: function(data){
		console.log('initialize task here')
		alert('start now')
	}
}
