spawn ui_window {
	wclass:"edited"
	clip:"true"
	area:"0.000000 0.000000 1024.000000 768.000000"
	coords:"0.000000 0.000000 1024.000000 768.000000"
	name:"window"
	spawn ui_group {
		id:"110"
		area:"357.000000 0.000000 1181.000000 768.000000"
		coords:"357.000000 0.000000 1181.000000 768.000000"
		name:"right"
		spawn ui_group {
			area:"9.000000 33.000000 1033.000000 801.000000"
			coords:"0.000000 0.000000 667.000000 768.000000"
			name:"frame"
			spawn ui_text {
				text:"<img src=shaders/ui/teil01_lef.shd>"
				area:"-11.000000 81.000000 53.000000 145.000000"
				coords:"-11.000000 81.000000 53.000000 145.000000"
			}
			spawn ui_text {
				text:"<img src=shaders/ui/teil01_rig.shd>"
				area:"604.000000 81.000000 668.000000 145.000000"
				coords:"604.000000 81.000000 668.000000 145.000000"
			}
			spawn ui_text {
				text:"<img src=shaders/ui/teil01_rig.shd>"
				area:"604.000000 491.000000 668.000000 555.000000"
				coords:"604.000000 491.000000 668.000000 555.000000"
			}
			spawn ui_text {
				text:"<img src=shaders/ui/teil01_lef.shd>"
				area:"-11.000000 491.000000 53.000000 555.000000"
				coords:"-11.000000 491.000000 53.000000 555.000000"
			}
			spawn frm_common {
				area:"28.000000 19.000000 628.000000 619.000000"
				coords:"28.000000 19.000000 628.000000 619.000000"
			}
			spawn txt_common {
				look:"3"
				value:"@@i_ctrl_top@@"
				area:"66.000000 70.000000 592.000000 102.000000"
				coords:"66.000000 70.000000 592.000000 102.000000"
			}
			spawn ui_group {
				area:"52.000000 178.000000 601.000000 234.000000"
				coords:"52.000000 178.000000 601.000000 234.000000"
				spawn btn_common {
					look:"3"
					value:"@@i_ctrl_bindings@@"
					type:"radio"
					tab:"4"
					id:"1010"
					tooltip:"@@i_ctrl_bindings_tt@@"
					area:"101.000000 22.999992 265.000000 53.000019"
					coords:"101.000000 22.999992 265.000000 53.000019"
				}
				spawn txt_common {
					look:"3"
					area:"90.000000 22.000015 269.000000 54.000015"
					coords:"90.000000 22.000015 269.000000 54.000015"
				}
				spawn btn_common {
					look:"3"
					value:"@@i_ctrl_joyset@@"
					type:"radio"
					tab:"5"
					id:"1020"
					tooltip:"@@i_ctrl_joyset_tt@@"
					area:"288.000000 25.000000 452.000000 52.000015"
					coords:"288.000000 25.000000 452.000000 52.000015"
				}
				spawn txt_common {
					look:"3"
					area:"277.000000 22.000015 456.000000 54.000015"
					coords:"277.000000 22.000015 456.000000 54.000015"
				}
			}
		}
		spawn ui_group {
			area:"50.000000 161.000000 622.000000 594.000000"
			coords:"50.000000 161.000000 622.000000 594.000000"
			name:"settings"
			spawn txt_common {
				look:"1"
				value:"@@i_ctrl_mouse_sens@@ :"
				area:"0.000000 -16.000000 232.000000 8.000000"
				coords:"0.000000 -16.000000 232.000000 8.000000"
				name:"sensitivity"
			}
			spawn sld_common {
				tab:"1"
				id:"2"
				tooltip:"@@i_ctrl_mouse_sens_tt@@"
				area:"237.000000 -16.000000 469.000000 7.999985"
				coords:"237.000000 -16.000000 469.000000 7.999985"
				name:"sensitivity"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_ctrl_inv_mouse@@ :"
				area:"0.000000 8.000000 232.000000 32.000008"
				coords:"0.000000 8.000000 232.000000 32.000008"
				name:"invert"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_ctrl_transmis@@ :"
				area:"0.000000 -40.000000 232.000000 -16.000000"
				coords:"0.000000 -40.000000 232.000000 -16.000000"
				name:"transmission"
			}
			spawn btn_common {
				look:"3"
				tab:"2"
				id:"3"
				tooltip:"@@i_ctrl_inv_mouse_tt@@"
				area:"237.000000 7.999992 297.000000 32.000000"
				coords:"237.000000 7.999992 297.000000 32.000000"
				name:"invert"
			}
			spawn btn_common {
				look:"3"
				value:"manual/auto"
				tab:"3"
				id:"4"
				tooltip:"@@i_ctrl_transmis_tt@@"
				area:"237.000000 -40.000000 332.799988 -19.474998"
				coords:"237.000000 -40.000000 409.000000 -16.000000"
				name:"transmission"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_ctrl_mouse_filt@@ :"
				area:"300.000000 7.999992 467.000000 32.000000"
				coords:"300.000000 7.999992 467.000000 32.000000"
				name:"filter"
			}
			spawn btn_common {
				look:"3"
				tab:"4"
				id:"6"
				tooltip:"@@i_ctrl_mouse_filt_tt@@"
				area:"471.000000 7.999992 546.000000 32.000000"
				coords:"471.000000 7.999992 546.000000 32.000000"
				name:"filter"
			}
		}
		spawn ui_group {
			id:"1200"
			area:"106.000000 229.000000 562.000000 577.000000"
			coords:"106.000000 229.000000 562.000000 577.000000"
			name:"key bindings"
			spawn sld_common {
				action_on_drag:"true"
				id:"93"
				area:"419.000000 35.000000 447.000000 347.000000"
				coords:"419.000000 35.000000 447.000000 347.000000"
				name:"slider"
			}
			spawn ui_group {
				clip:"true"
				id:"94"
				area:"0.000000 36.000000 421.000000 348.000000"
				coords:"0.000000 36.000000 421.000000 348.000000"
				name:"clip"
				spawn txt_common {
					look:"2"
					value:"Impulse name"
					id:"90"
					area:"0.000000 0.000000 162.000000 26.000000"
					coords:"0.000000 0.000000 162.000000 26.000000"
					name:"name"
				}
				spawn btn_common {
					look:"15"
					value:"Right Arrow + Left Arrow"
					id:"91"
					tooltip:"code1"
					area:"168.000000 0.000008 420.000000 26.000000"
					coords:"168.000000 0.000000 420.000000 26.000000"
					name:"codes"
				}
				spawn ui_group {
					id:"101"
					area:"0.000000 -0.000023 32.000000 31.999977"
					coords:"0.000000 -0.000023 32.000000 31.999977"
					name:"impulse group"
				}
			}
			spawn txt_common {
				look:"17"
				value:"@@i_ctrl_binding@@"
				area:"3.000000 12.000015 161.000000 37.000042"
				coords:"3.000000 12.000015 161.000000 37.000042"
			}
			spawn txt_common {
				look:"17"
				value:"@@i_ctrl_primsec@@"
				area:"167.000000 12.000015 418.000000 37.000042"
				coords:"167.000000 12.000015 418.000000 37.000042"
			}
		}
		spawn ui_group {
			id:"1300"
			area:"51.000000 231.000000 623.000000 589.000000"
			coords:"51.000000 231.000000 623.000000 589.000000"
			name:"joy settings"
			spawn txt_common {
				look:"1"
				value:"@@i_ctrl_joystick@@ :"
				area:"12.000000 10.000000 140.000000 34.000000"
				coords:"12.000000 10.000000 230.000000 34.000000"
				name:"joy"
			}
			spawn btn_common {
				look:"20"
				value:"X-button X-axis joy"
				tab:"1"
				id:"700"
				tooltip:"@@i_ctrl_joysel_tt@@"
				area:"237.000000 10.000000 609.000000 34.000000"
				coords:"237.000000 10.000000 609.000000 34.000000"
				name:"joy"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_ctrl_force_feedb@@ :"
				area:"12.000000 59.000000 230.000000 83.000000"
				coords:"12.000000 59.000000 230.000000 83.000000"
				name:"force feedback"
			}
			spawn btn_common {
				look:"3"
				value:"Yes/NO"
				tab:"3"
				id:"704"
				tooltip:"@@i_ctrl_force_feedb_tt@@"
				area:"237.000000 59.000000 527.000000 83.000000"
				coords:"237.000000 59.000000 527.000000 83.000000"
				name:"force feedback"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_ctrl_feedb_gain@@ :"
				area:"12.000000 83.000000 176.000000 107.000000"
				coords:"12.000000 83.000000 230.000000 107.000000"
				name:"fgain"
			}
			spawn sld_common {
				tab:"4"
				id:"705"
				tooltip:"@@i_ctrl_feedb_gain_tt@@"
				area:"237.000000 80.000000 469.000000 103.999985"
				coords:"237.000000 80.000000 469.000000 103.999985"
				name:"fgain"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_ctrl_wheel@@ :"
				area:"12.000000 35.000000 140.000000 59.000000"
				coords:"12.000000 35.000000 230.000000 59.000000"
				name:"wheel"
			}
			spawn btn_common {
				look:"3"
				value:"Yes/No"
				tab:"2"
				id:"701"
				tooltip:"@@i_ctrl_wheel_tt@@"
				area:"237.000000 34.000000 294.799988 54.525002"
				coords:"237.000000 34.000000 365.000000 58.000000"
				name:"wheel"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_ctrl_sensitivity@@ :"
				area:"12.000000 107.000000 140.000000 131.000000"
				coords:"12.000000 107.000000 230.000000 131.000000"
				name:"sensitivity"
			}
			spawn sld_common {
				tab:"5"
				id:"702"
				tooltip:"@@i_ctrl_sensitivity_tt@@"
				area:"237.000000 104.000000 469.000000 127.999985"
				coords:"237.000000 104.000000 469.000000 127.999985"
				name:"sensitivity"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_ctrl_steer_axis@@ :"
				area:"12.000000 131.000000 176.000000 155.000000"
				coords:"12.000000 131.000000 230.000000 155.000000"
				name:"asteer"
			}
			spawn btn_common {
				look:"20"
				value:"Axis 0"
				tab:"6"
				id:"706"
				tooltip:"@@i_ctrl_steer_axis_tt@@"
				area:"237.000000 130.000000 527.000000 154.000000"
				coords:"237.000000 130.000000 527.000000 154.000000"
				name:"asteer"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_ctrl_deadzone@@"
				area:"13.000000 155.000000 310.000000 179.000000"
				coords:"13.000000 155.000000 310.000000 179.000000"
				name:"dead zone"
			}
			spawn sld_common {
				tab:"7"
				id:"503"
				tooltip:"@@i_ctrl_deadzone_tt@@"
				area:"313.000000 152.000000 477.000000 175.999985"
				coords:"313.000000 152.000000 477.000000 175.999985"
				name:"dead zone"
			}
			spawn txt_common {
				look:"3"
				area:"222.000000 184.000000 478.000000 208.000000"
				coords:"222.000000 184.000000 478.000000 208.000000"
			}
			spawn txt_common {
				look:"17"
				value:"<img src=shaders/ui/white.shd color=a0a0a0a0 right=30 yscale=stretch>"
				id:"500"
				area:"222.000000 184.000000 478.000000 208.000000"
				coords:"222.000000 184.000000 478.000000 208.000000"
				name:"dead"
			}
			spawn txt_common {
				look:"1"
				value:"<img src=shaders/ui/white.shd right=20 yscale=stretch>"
				id:"501"
				area:"222.000000 188.000000 350.000000 203.000000"
				coords:"222.000000 188.000000 350.000000 203.000000"
				name:"joy left"
			}
			spawn txt_common {
				value:"<img src=shaders/ui/white.shd right=20 yscale=stretch>"
				id:"502"
				area:"350.000000 188.000000 478.000000 203.000000"
				coords:"350.000000 188.000000 478.000000 203.000000"
				name:"joy right"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_ctrl_acc_axis@@ :"
				area:"12.000000 212.000000 201.999969 236.000000"
				coords:"12.000000 212.000000 230.000000 236.000000"
				name:"athrottle"
			}
			spawn btn_common {
				look:"20"
				value:"Axis 1"
				tab:"8"
				id:"707"
				tooltip:"@@i_ctrl_acc_axis_tt@@"
				area:"237.000000 211.000000 527.000000 235.000000"
				coords:"237.000000 211.000000 527.000000 235.000000"
				name:"athrottle"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_ctrl_deadzone@@"
				area:"13.000000 235.000000 100.000000 259.000000"
				coords:"13.000000 235.000000 310.000000 259.000000"
				name:"dead zone"
			}
			spawn sld_common {
				tab:"9"
				id:"513"
				tooltip:"@@i_ctrl_acc_deadz_tt@@"
				area:"313.000000 233.000000 477.000000 257.000000"
				coords:"313.000000 233.000000 477.000000 257.000000"
				name:"dead zone"
			}
			spawn txt_common {
				look:"3"
				area:"222.000000 264.000000 478.000000 288.000000"
				coords:"222.000000 264.000000 478.000000 288.000000"
			}
			spawn txt_common {
				look:"17"
				value:"<img src=shaders/ui/white.shd color=a0a0a0a0 right=30 yscale=stretch>"
				id:"510"
				area:"222.000000 264.000000 478.000000 288.000000"
				coords:"222.000000 264.000000 478.000000 288.000000"
				name:"dead"
			}
			spawn txt_common {
				look:"1"
				value:"<img src=shaders/ui/white.shd right=20 yscale=stretch>"
				id:"511"
				area:"222.000000 269.000000 350.000000 284.000000"
				coords:"222.000000 269.000000 350.000000 284.000000"
				name:"joy left"
			}
			spawn txt_common {
				value:"<img src=shaders/ui/white.shd right=20 yscale=stretch>"
				id:"512"
				area:"350.000000 269.000000 478.000000 284.000000"
				coords:"350.000000 269.000000 478.000000 284.000000"
				name:"joy right"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_ctrl_brk_axis@@ :"
				area:"12.000000 293.000000 201.999969 317.000000"
				coords:"12.000000 292.000000 230.000000 316.000000"
				name:"abrake"
			}
			spawn btn_common {
				look:"20"
				value:"Axis 1"
				tab:"10"
				id:"708"
				tooltip:"@@i_ctrl_brk_axis_tt@@"
				area:"237.000000 291.000000 527.000000 315.000000"
				coords:"237.000000 291.000000 527.000000 315.000000"
				name:"abrake"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_ctrl_deadzone@@"
				area:"13.000000 315.000000 100.000000 339.000000"
				coords:"13.000000 315.000000 310.000000 339.000000"
				name:"dead zone"
			}
			spawn sld_common {
				tab:"11"
				id:"523"
				tooltip:"@@i_ctrl_brk_deadz_tt@@"
				area:"313.000000 313.000000 477.000000 337.000000"
				coords:"313.000000 313.000000 477.000000 337.000000"
				name:"dead zone"
			}
			spawn txt_common {
				look:"17"
				value:"<img src=shaders/ui/white.shd color=a0a0a0a0 right=30 yscale=stretch>"
				id:"520"
				area:"222.000000 344.000000 478.000000 368.000000"
				coords:"222.000000 344.000000 478.000000 368.000000"
				name:"dead"
			}
			spawn txt_common {
				look:"3"
				area:"222.000000 345.000000 478.000000 369.000000"
				coords:"222.000000 344.000000 478.000000 368.000000"
			}
			spawn txt_common {
				look:"1"
				value:"<img src=shaders/ui/white.shd right=20 yscale=stretch>"
				id:"521"
				area:"222.000000 349.000000 350.000000 364.000000"
				coords:"222.000000 349.000000 350.000000 364.000000"
				name:"joy left"
			}
			spawn txt_common {
				value:"<img src=shaders/ui/white.shd right=20 yscale=stretch>"
				id:"522"
				area:"350.000000 349.000000 478.000000 364.000000"
				coords:"350.000000 349.000000 478.000000 364.000000"
				name:"joy right"
			}
		}
	}
}
