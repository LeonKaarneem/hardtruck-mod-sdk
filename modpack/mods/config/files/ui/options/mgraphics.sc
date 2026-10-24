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
				value:"@@i_gfx_perf_set@@"
				area:"66.000000 70.000000 592.000000 102.000000"
				coords:"66.000000 70.000000 592.000000 102.000000"
			}
			spawn txt_common {
				look:"3"
				value:"@@i_gfx_adv_set@@"
				area:"66.000000 270.000000 592.000000 302.000000"
				coords:"66.000000 270.000000 592.000000 302.000000"
			}
		}
		spawn ui_group {
			area:"49.000000 122.000000 603.000000 265.000000"
			coords:"49.000000 122.000000 603.000000 265.000000"
			name:"base controls"
			spawn txt_common {
				look:"1"
				value:"@@i_gfx_view_dist@@ :"
				area:"0.000000 0.000000 232.000000 24.000000"
				coords:"0.000000 0.000000 232.000000 24.000000"
				name:"vdist"
			}
			spawn sld_common {
				tab:"1"
				id:"501"
				tooltip:"@@i_gfx_view_dist_tt@@"
				area:"237.000000 -3.000000 469.000000 21.000008"
				coords:"237.000000 -3.000000 469.000000 21.000008"
				name:"vdist"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_gfx_lod_dist@@ :"
				area:"0.000000 24.000000 232.000000 47.999985"
				coords:"0.000000 24.000000 232.000000 47.999985"
				name:"lswitch"
			}
			spawn sld_common {
				tab:"2"
				id:"502"
				tooltip:"@@i_gfx_lod_dist_tt@@"
				area:"237.000000 22.000000 469.000000 45.999985"
				coords:"237.000000 22.000000 469.000000 45.999985"
				name:"lswitch"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_gfx_partic_int@@ :"
				area:"0.000000 48.000000 232.000000 71.999985"
				coords:"0.000000 48.000000 232.000000 71.999985"
				name:"pintensity"
			}
			spawn sld_common {
				tab:"3"
				id:"503"
				tooltip:"@@i_gfx_partic_int_tt@@"
				area:"237.000000 46.000000 469.000000 69.999985"
				coords:"237.000000 46.000000 469.000000 69.999985"
				name:"pintensity"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_gfx_traffic@@ :"
				area:"0.000000 72.000000 232.000000 95.999985"
				coords:"0.000000 72.000000 232.000000 95.999985"
				name:"tdensity"
			}
			spawn sld_common {
				tab:"4"
				id:"506"
				tooltip:"@@i_gfx_traffic_tt@@"
				area:"237.000000 70.000008 469.000000 93.999992"
				coords:"237.000000 70.000008 469.000000 93.999992"
				name:"tdensity"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_gfx_shadows@@ :"
				area:"0.000000 95.000000 232.000000 119.000008"
				coords:"0.000000 95.000000 232.000000 119.000008"
				name:"seffec"
			}
			spawn btn_common {
				look:"3"
				tab:"5"
				id:"504"
				tooltip:"@@i_gfx_shadows_tt@@"
				area:"237.000000 95.000000 469.000000 119.000008"
				coords:"237.000000 95.000000 469.000000 119.000008"
				name:"seffec"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_gfx_lights@@ :"
				area:"0.000000 120.000000 232.000000 144.000000"
				coords:"0.000000 120.000000 232.000000 144.000000"
				name:"lights"
			}
			spawn btn_common {
				look:"3"
				tab:"6"
				id:"505"
				tooltip:"@@i_gfx_lights_tt@@"
				area:"237.000000 120.000000 469.000000 144.000000"
				coords:"237.000000 120.000000 469.000000 144.000000"
				name:"lights"
			}
		}
		spawn ui_group {
			area:"49.000000 322.000000 608.000000 600.000000"
			coords:"49.000000 322.000000 608.000000 600.000000"
			name:"advanced controls"
			spawn txt_common {
				look:"1"
				value:"@@i_gfx_driver@@ :"
				area:"1.000000 0.000000 233.000000 24.000000"
				coords:"1.000000 0.000000 233.000000 24.000000"
				name:"driver"
			}
			spawn btn_common {
				look:"20"
				tab:"101"
				id:"1"
				tooltip:"@@i_gfx_driver_tt@@"
				area:"238.000000 0.000000 547.000000 24.000008"
				coords:"238.000000 0.000000 547.000000 24.000008"
				name:"driver"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_gfx_renderer@@ :"
				area:"1.000000 24.000000 233.000000 47.999985"
				coords:"1.000000 24.000000 233.000000 47.999985"
				name:"renderer"
			}
			spawn btn_common {
				look:"19"
				tab:"102"
				id:"2"
				tooltip:"@@i_gfx_renderer_tt@@"
				area:"238.000000 27.000000 548.000000 71.000000"
				coords:"238.000000 27.000000 548.000000 71.000000"
				name:"renderer"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_gfx_vid_mode@@ :"
				area:"1.000000 73.500000 233.000000 97.500000"
				coords:"1.000000 73.500000 233.000000 97.500000"
				name:"mode"
			}
			spawn btn_common {
				look:"20"
				tab:"103"
				id:"3"
				tooltip:"@@i_gfx_vid_mode_tt@@"
				area:"238.000000 73.500000 547.000000 97.500000"
				coords:"238.000000 73.500000 547.000000 97.500000"
				name:"mode"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_gfx_col_depth@@ :"
				area:"1.000000 98.000000 233.000000 122.000000"
				coords:"1.000000 98.000000 233.000000 122.000000"
				name:"cdepth"
			}
			spawn btn_common {
				look:"20"
				tab:"104"
				id:"4"
				tooltip:"@@i_gfx_col_depth_tt@@"
				area:"238.000000 98.000000 547.000000 122.000000"
				coords:"238.000000 98.000000 547.000000 122.000000"
				name:"cdepth"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_gfx_fullscreen@@ :"
				area:"1.000000 121.500015 233.000000 145.500015"
				coords:"1.000000 121.500015 233.000000 145.500015"
				name:"fcreen"
			}
			spawn btn_common {
				look:"3"
				tab:"105"
				id:"5"
				tooltip:"@@i_gfx_fullscreen_tt@@"
				area:"238.000000 121.500015 547.000000 145.500015"
				coords:"238.000000 121.500015 547.000000 145.500015"
				name:"fscreen"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_gfx_vsync@@ :"
				area:"1.000000 145.500000 233.000000 169.500000"
				coords:"1.000000 145.500000 233.000000 169.500000"
				name:"fsync"
			}
			spawn btn_common {
				look:"3"
				tab:"106"
				id:"6"
				tooltip:"@@i_gfx_vsync_tt@@"
				area:"238.000000 145.500000 547.000000 169.500000"
				coords:"238.000000 145.500000 547.000000 169.500000"
				name:"fsync"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_gfx_brightness@@ :"
				area:"1.000000 169.500000 233.000000 193.500000"
				coords:"1.000000 169.500000 233.000000 193.500000"
				name:"gamma"
			}
			spawn sld_common {
				action_on_drag:"true"
				tab:"107"
				id:"7"
				tooltip:"@@i_gfx_brightness_tt@@"
				area:"238.000000 169.500000 470.000000 193.500000"
				coords:"238.000000 169.500000 470.000000 193.500000"
				name:"gamma"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_gfx_tex_qual@@ :"
				area:"1.000000 193.500031 233.000000 217.500031"
				coords:"1.000000 193.500031 233.000000 217.500031"
				name:"tquality"
			}
			spawn btn_common {
				look:"20"
				tab:"108"
				id:"16"
				tooltip:"@@i_gfx_tex_qual_tt@@"
				area:"238.000000 193.500031 470.000000 217.500031"
				coords:"238.000000 193.500031 470.000000 217.500031"
				name:"tquality"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_gfx_tex_det@@ :"
				area:"1.000000 217.500031 233.000000 241.500031"
				coords:"1.000000 217.500031 233.000000 241.500031"
				name:"tdetail"
			}
			spawn btn_common {
				look:"3"
				tab:"109"
				id:"17"
				tooltip:"@@i_gfx_tex_det_tt@@"
				area:"238.000000 217.500031 470.000000 241.500031"
				coords:"238.000000 217.500031 470.000000 241.500031"
				name:"tdetail"
			}
			spawn txt_common {
				look:"1"
				value:"@@i_gfx_tex_filt@@ :"
				area:"1.000000 241.500000 233.000000 265.500000"
				coords:"1.000000 241.500000 233.000000 265.500000"
				name:"tfilter"
			}
			spawn btn_common {
				look:"3"
				tab:"110"
				id:"18"
				tooltip:"@@i_gfx_tex_filt_tt@@"
				area:"238.000000 241.500000 470.000000 265.500000"
				coords:"238.000000 241.500000 470.000000 265.500000"
				name:"tfilter"
			}
		}
	}
}
