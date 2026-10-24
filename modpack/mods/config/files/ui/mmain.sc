spawn ui_window {
	wclass:"edited"
	clip:"true"
	area:"0.000000 0.000000 1024.000000 768.000000"
	coords:"0.000000 0.000000 1024.000000 768.000000"
	name:"window"
	spawn ui_group {
		id:"120"
		area:"101.000000 26.000000 1125.000000 794.000000"
		coords:"64.000000 0.000000 320.000000 768.000000"
		name:"middle"
		spawn ui_text {
			text:"<img src=shaders/ui/pipe_vert.shd>"
			area:"108.522324 640.000000 125.267975 901.000000"
			coords:"110.000000 640.000000 142.000000 768.000000"
		}
		spawn ui_text {
			text:"<img src=shaders/ui/menu_mid03.shd><br><img src=shaders/ui/menu_mid02.shd><br><img src=shaders/ui/menu_mid01.shd>"
			area:"0.000000 0.000000 256.000000 768.000000"
			coords:"0.000000 0.000000 256.000000 768.000000"
		}
		spawn ui_text {
			text:"<font face=ui_small.font xscale=0.41 yscale=0.41 spacing=2><right>v 1.2</right><font>"
			coords:"57.000000 128.000000 200.000000 768.000000"
			area:"0.000000 128.000000 256.000000 768.000000"
		}
		spawn btn_common {
			look:"21"
			value:"@@i_west_coast@@"
			tooltip:"@@i_easy_tt@@"
			tab:"1"
			id:"2"
			area:"34.000000 156.000000 242.000000 204.000000"
			coords:"0.000000 140.000000 256.000000 204.000000"
			name:"new game"
		}
		spawn btn_common {
			look:"21"
			value:"@@i_midwest@@"
			tooltip:"@@i_medium_tt@@"
			tab:"2"
			id:"3"
			area:"34.000000 207.000000 242.000000 255.000000"
			coords:"0.000000 191.000000 256.000000 255.000000"
			name:"new game"
		}
		spawn btn_common {
			look:"21"
			value:"@@i_rockies@@"
			tooltip:"@@i_hard_tt@@"
			tab:"3"
			id:"4"
			area:"34.000000 259.000000 242.000000 307.000000"
			coords:"0.000000 243.000000 256.000000 307.000000"
			name:"new game"
		}
		spawn btn_common {
			look:"21"
			value:"@@i_load_game@@"
			tab:"4"
			id:"5"
			tooltip:"@@i_load_tt@@"
			area:"34.000000 353.000000 242.000000 401.000000"
			coords:"0.000000 337.000000 256.000000 401.000000"
			name:"options"
		}
		spawn btn_common {
			look:"21"
			value:"@@i_options@@"
			tab:"5"
			id:"10"
			tooltip:"@@i_options_tt@@"
			area:"34.000000 411.000000 242.000000 459.000000"
			coords:"0.000000 395.000000 256.000000 459.000000"
			name:"options"
		}
		spawn btn_common {
			look:"21"
			value:"@@i_credits@@"
			tab:"6"
			id:"11"
			tooltip:"@@i_credits_tt@@"
			area:"34.000000 469.000000 242.000000 517.000000"
			coords:"0.000000 453.000000 256.000000 517.000000"
			name:"credits"
		}
		spawn ui_text {
			text:"<img src=shaders/ui/ring1.shd>"
			area:"94.000000 515.000000 158.000000 579.000000"
			coords:"94.000000 515.000000 158.000000 579.000000"
		}
		spawn btn_common {
			look:"5"
			value:"x"
			tab:"7"
			id:"1"
			tooltip:"@@i_quit_tt@@"
			area:"94.000000 515.000000 158.000000 579.000000"
			coords:"94.000000 515.000000 158.000000 579.000000"
			name:"exit"
		}
	}
	spawn txt_common {
		value:"Hard Truck Modern v3"
		look:"1"
		coords:"704.000000 716.000000 1008.000000 740.000000"
		area:"704.000000 716.000000 1008.000000 740.000000"
	}
}
