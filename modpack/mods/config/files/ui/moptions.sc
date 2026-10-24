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
		spawn ui_group {
			area:"28.066956 140.000000 226.511047 443.000000"
			coords:"28.066956 140.000000 226.511047 443.000000"
			spawn btn_common {
				look:"21"
				value:"@@i_graphics@@"
				type:"radio"
				tab:"1"
				id:"10"
				area:"5.933044 16.000008 193.933044 48.000008"
				coords:"-28.066956 0.000008 227.933044 64.000008"
				name:"graphics"
			}
			spawn btn_common {
				look:"21"
				value:"@@i_sound@@"
				type:"radio"
				tab:"2"
				id:"11"
				area:"5.933044 60.000008 193.933044 92.000008"
				coords:"-28.066956 44.000008 227.933044 108.000008"
				name:"sound"
			}
			spawn btn_common {
				look:"21"
				value:"@@i_controls@@"
				type:"radio"
				tab:"3"
				id:"12"
				area:"5.933044 103.999992 193.933044 136.000000"
				coords:"-28.066956 87.999992 227.933044 152.000000"
				name:"controls"
			}
		}
		spawn btn_common {
			value:"@@i_defaults@@
			tab:"5"
			id:"3"
			tooltip:"@@i_load_defaults@@"
			area:"0.000000 438.000000 256.000000 502.000000"
			coords:"0.000000 438.000000 256.000000 502.000000"
			name:"defaults"
		}
		spawn ui_text {
			text:"<img src=shaders/ui/ring1.shd>"
			area:"39.000000 515.000000 103.000000 579.000000"
			coords:"39.000000 515.000000 103.000000 579.000000"
		}
		spawn ui_text {
			text:"<img src=shaders/ui/ring1.shd>"
			area:"149.000000 515.000000 213.000000 579.000000"
			coords:"149.000000 515.000000 213.000000 579.000000"
		}
		spawn btn_common {
			look:"5"
			value:"v"
			tab:"6"
			id:"1"
			tooltip:"@@i_apply_settings@@"
			area:"39.000000 515.000000 103.000000 579.000000"
			coords:"39.000000 515.000000 103.000000 579.000000"
			name:"ok"
		}
		spawn btn_common {
			look:"5"
			value:"x"
			tab:"7"
			id:"2"
			tooltip:"@@i_cancel_settings@@"
			area:"149.000000 515.000000 213.000000 579.000000"
			coords:"149.000000 515.000000 213.000000 579.000000"
			name:"cancel"
		}
	}
}
