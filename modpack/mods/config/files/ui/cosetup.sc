spawn ui_window {
	wclass:"edited"
	clip:"true"
	area:"0.000000 0.000000 1024.000000 768.000000"
	coords:"0.000000 0.000000 1024.000000 768.000000"
	name:"window"
	spawn frm_common {
		look:"3"
		area:"242.000000 153.000000 782.000000 615.000000"
		coords:"242.000000 153.000000 782.000000 615.000000"
	}
	spawn txt_common {
		look:"3"
		value:"UNOFFICIAL MOD - Hard Truck Modern v3<br>Improves handling, physics, cameras, economy and AI<br>Installed features depend on Setup choices"
		area:"262.000000 625.000000 762.000000 705.000000"
		coords:"262.000000 625.000000 762.000000 705.000000"
	}
	spawn txt_common {
		look:"10"
		value:"@@cos_top@@"
		area:"329.000000 184.000000 700.999634 220.000000"
		coords:"329.000000 184.000000 700.999634 220.000000"
	}
	spawn ui_text {
		text:"<img src=shaders/ui/i_start.shd xscale=stretch yscale=stretch color=40ffffff>"
		area:"318.000000 194.000000 702.000000 578.000000"
		coords:"318.000000 194.000000 702.000000 578.000000"
	}
	spawn txt_common {
		look:"8"
		value:"@@cos_owner@@ :"
		area:"265.000000 288.000000 470.000000 320.000000"
		coords:"265.000000 288.000000 470.000000 320.000000"
	}
	spawn inp_common {
		limit:"26"
		id:"10"
		area:"475.000000 252.000000 753.000000 283.000000"
		coords:"475.000000 252.000000 753.000000 283.000000"
		name:"coname"
	}
	spawn inp_common {
		limit:"26"
		id:"11"
		area:"475.000000 289.000000 754.000000 320.000000"
		coords:"475.000000 289.000000 754.000000 320.000000"
		name:"owname"
	}
	spawn txt_common {
		look:"8"
		value:"@@cos_compname@@ :"
		area:"265.000000 251.000000 464.000000 283.000000"
		coords:"265.000000 251.000000 470.000000 283.000000"
	}
	spawn txt_common {
		look:"7"
		value:"@@cos_txt1@@<BR> <BR>@@cos_txt2@@"
		area:"277.000000 347.000000 754.000000 556.000000"
		coords:"277.000000 347.000000 754.000000 556.000000"
	}
	spawn btn_common {
		look:"6"
		value:"@@cos_ok@@"
		tab:"8"
		id:"1"
		area:"449.000000 566.000000 584.000000 590.000000"
		coords:"449.000000 566.000000 584.000000 590.000000"
		name:"ok"
	}
}
