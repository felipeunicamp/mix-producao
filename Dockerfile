FROM public.ecr.aws/lambda/python:3.11                                                                   
                                                                                                           
  RUN yum install -y glpk && yum clean all                                                                 
                                                                                                           
  COPY requirements.txt .                                                                                  
  RUN pip install --no-cache-dir -r requirements.txt                                                       
                                                                                                           
  COPY mix_attempt1_celulose_lambda.py .                                                                   
  COPY lambda_function.py .                                                                                
                                                                                                           
  CMD ["lambda_function.lambda_handler"]
