from pie_data import PIE
pie_path = "/mnt/mars-beta/izzeddin/PIEPredict/PIE_dataset/"
imdb = PIE(data_path=pie_path)
imdb.extract_and_save_images(extract_frame_type='annotated')